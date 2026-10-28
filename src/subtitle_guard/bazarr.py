"""Bazarr integration: post-processing hook, retries through the blacklist, and library scans."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from .guard import evaluate_and_fix
from .outcome import DECISION, describe
from .storage import STATE_DIRECTORY, load_state, now, report, save_state


BAZARR_URL = os.environ.get("BAZARR_URL", "http://localhost:6767")
BAZARR_CONFIG = os.environ.get("BAZARR_CONFIG", "/config/config/config.yaml")
MAX_SUBTITLES_PER_MEDIA = 4
RETRY_WAIT_SECONDS = 900
NEIGHBOUR_EPISODES = 2


def handle_download(video, subtitle, language, provider, subtitle_id, media_id, series_id=""):
    state = load_state(video, subtitle)
    if state.get("video_size") != os.path.getsize(video):
        state = {"video_size": os.path.getsize(video), "attempts": []}
    series_number = int(series_id) if series_id.isdigit() else None
    media_number = int(media_id) if media_id.isdigit() else None
    neighbours = series_neighbours(series_number, video) if series_number else ()
    outcome = evaluate_and_fix(video, subtitle, apply=True, neighbours=neighbours)
    state["attempts"].append({"time": now(), "provider": provider, "subtitle_id": subtitle_id, "subtitle": subtitle, **outcome})
    save_state(video, subtitle, state)
    report(subtitle, provider, outcome)
    print(describe(outcome))
    if outcome["decision"] != DECISION["rejected"] or media_number is None:
        return 0
    if len(state["attempts"]) >= MAX_SUBTITLES_PER_MEDIA:
        restore_best(subtitle, state)
        return 0
    state["pending_retry"] = {"media_type": "series" if series_number else "movie", "media_id": media_number, "series_id": series_number,
                              "subtitle": subtitle, "provider": provider, "subtitle_id": subtitle_id, "language": language}
    save_state(video, subtitle, state)
    log_file = open(os.path.join(STATE_DIRECTORY, "retries.log"), "a")
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (package_parent, os.environ.get("PYTHONPATH"))))}
    subprocess.Popen([sys.executable, "-m", "subtitle_guard", "try-another", video, subtitle], stdin=subprocess.DEVNULL,
                     stdout=log_file, stderr=log_file, start_new_session=True, env=environment)
    print("subtitle-guard: asking Bazarr for another subtitle in the background")
    return 0


def scan_series(series_id, apply):
    episodes = sorted((episode for episode in bazarr_get(f"/api/episodes?seriesid[]={series_id}")["data"] if episode.get("path")),
                      key=lambda item: (item["season"], item["episode"]))
    for position, episode in enumerate(episodes):
        nearby = episodes[max(0, position - NEIGHBOUR_EPISODES):position] + episodes[position + 1:position + NEIGHBOUR_EPISODES + 1]
        neighbours = [(f"S{other['season']:02d}E{other['episode']:02d}", other["path"]) for other in nearby]
        for subtitle in episode.get("subtitles", []):
            if subtitle.get("path"):
                label = f"S{episode['season']:02d}E{episode['episode']:02d}"
                scan_one(label, episode["path"], subtitle, apply, neighbours)
    return 0


def scan_movies(apply):
    for movie in bazarr_get("/api/movies?start=0&length=-1")["data"]:
        for subtitle in movie.get("subtitles", []):
            if movie.get("path") and subtitle.get("path"):
                scan_one(movie.get("title", "movie"), movie["path"], subtitle, apply, ())
    return 0


def scan_one(label, video, subtitle, apply, neighbours):
    try:
        outcome = evaluate_and_fix(video, subtitle["path"], apply, neighbours)
    except (OSError, ValueError) as error:
        print(f"{label} {subtitle['code2']}: error {error}", flush=True)
        return
    if apply:
        report(subtitle["path"], "scan", outcome)
    print(f"{label} {subtitle['code2']}: {describe(outcome)}", flush=True)


def series_neighbours(series_id, video):
    try:
        episodes = sorted((episode for episode in bazarr_get(f"/api/episodes?seriesid[]={series_id}")["data"] if episode.get("path")),
                          key=lambda item: (item["season"], item["episode"]))
    except OSError:
        return ()
    position = next((index for index, episode in enumerate(episodes) if episode["path"] == video), None)
    if position is None:
        return ()
    nearby = episodes[max(0, position - NEIGHBOUR_EPISODES):position] + episodes[position + 1:position + NEIGHBOUR_EPISODES + 1]
    return [(f"S{episode['season']:02d}E{episode['episode']:02d}", episode["path"]) for episode in nearby]


def try_another_subtitle(video, subtitle):
    time.sleep(20)
    state = load_state(video, subtitle)
    pending = state.pop("pending_retry", None)
    save_state(video, subtitle, state)
    if not pending:
        return 0
    print(f"{now()} blacklisting {pending['provider']} {pending['subtitle_id']} for {subtitle}", flush=True)
    form = {"provider": pending["provider"], "subs_id": pending["subtitle_id"], "language": pending["language"],
            "subtitles_path": pending["subtitle"]}
    if pending["media_type"] == "series":
        form.update(seriesid=pending["series_id"], episodeid=pending["media_id"])
        endpoint = "/api/episodes/blacklist"
    else:
        form.update(radarrid=pending["media_id"])
        endpoint = "/api/movies/blacklist"
    attempts_before = len(state["attempts"])
    bazarr_post(endpoint, form)
    deadline = time.time() + RETRY_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(15)
        if len(load_state(video, subtitle).get("attempts", [])) > attempts_before or any_subtitle_for_language(pending):
            return 0
    print(f"{now()} no other subtitle found, restoring the best version", flush=True)
    restore_best(pending["subtitle"], load_state(video, subtitle))
    rescan(pending)
    return 0


def restore_best(subtitle, state):
    candidates = [attempt for attempt in state["attempts"] if attempt.get("subtitle") == subtitle and attempt.get("after")
                  and attempt.get("best_copy") and os.path.exists(attempt["best_copy"])]
    if not candidates:
        return
    best = max(candidates, key=lambda attempt: attempt["after"]["matched"])
    shutil.copy2(best["best_copy"], subtitle)
    report(subtitle, best.get("provider", ""), {**best, "decision": DECISION["best_effort"]})
    print(f"subtitle-guard: kept best effort ({best['after']['matched']:.2f} matched) from {best.get('provider')}")


def any_subtitle_for_language(pending):
    if pending["media_type"] == "series":
        item = bazarr_get(f"/api/episodes?episodeid[]={pending['media_id']}")["data"][0]
    else:
        item = bazarr_get(f"/api/movies?radarrid[]={pending['media_id']}")["data"][0]
    return any(subtitle["code2"] == pending["language"] and subtitle.get("path") for subtitle in item.get("subtitles", []))


def rescan(pending):
    if pending["media_type"] == "series":
        bazarr_patch("/api/series", {"seriesid": pending["series_id"], "action": "scan-disk"})
        return
    bazarr_patch("/api/movies", {"radarrid": pending["media_id"], "action": "scan-disk"})


def bazarr_get(path):
    return json.load(urllib.request.urlopen(urllib.request.Request(BAZARR_URL + path, headers={"X-API-KEY": api_key()}), timeout=60))


def bazarr_post(path, form):
    return bazarr_send(path, form, "POST")


def bazarr_patch(path, form):
    return bazarr_send(path, form, "PATCH")


def bazarr_send(path, form, method):
    request = urllib.request.Request(BAZARR_URL + path, data=urllib.parse.urlencode(form).encode(), method=method,
                                     headers={"X-API-KEY": api_key()})
    return urllib.request.urlopen(request, timeout=900).status


def api_key():
    if os.environ.get("BAZARR_API_KEY"):
        return os.environ["BAZARR_API_KEY"]
    match = re.search(r"^auth:\n(?:[ \t]+.*\n)*?[ \t]+apikey: *(\S+)", open(BAZARR_CONFIG).read(), re.M)
    return match.group(1).strip("'\"")
