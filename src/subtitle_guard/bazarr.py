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

from . import jobs
from .guard import evaluate_and_fix
from .logs import LEVEL, log
from .outcome import DECISION, describe
from .files import restore_version, save_version, subtitle_lock
from .storage import backup_path, load_state, now, report, save_state, state_path


BAZARR_URL = os.environ.get("BAZARR_URL", "http://localhost:6767")
BAZARR_CONFIG = os.environ.get("BAZARR_CONFIG", "/config/config/config.yaml")
MAX_SUBTITLES_PER_MEDIA = 4
RETRY_WAIT_SECONDS = 900
BLACKLIST_DELAY_SECONDS = 20
NEIGHBOUR_EPISODES = 2


def handle_download(video, subtitle, language, provider, subtitle_id, media_id, series_id=""):
    state = load_state(video, subtitle)
    if state.get("video_size") != os.path.getsize(video):
        state = {"video_size": os.path.getsize(video), "attempts": []}
    series_number = int(series_id) if series_id.isdigit() else None
    media_number = int(media_id) if media_id.isdigit() else None
    neighbours = series_neighbours(series_number, video) if series_number else ()
    outcome = evaluate_and_fix(video, subtitle, apply=True, neighbours=neighbours)
    state["attempts"].append({"time": now(), "provider": provider, "subtitle_id": subtitle_id, "subtitle": subtitle, "language": language,
                              "media_id": media_number, "series_id": series_number, **outcome})
    save_state(video, subtitle, state)
    report(subtitle, provider, outcome)
    if outcome["decision"] != DECISION["rejected"] or media_number is None:
        return outcome
    if len(state["attempts"]) >= MAX_SUBTITLES_PER_MEDIA:
        restore_best(subtitle, state)
        return outcome
    state["pending_retry"] = {"media_type": "series" if series_number else "movie", "media_id": media_number, "series_id": series_number,
                              "subtitle": subtitle, "provider": provider, "subtitle_id": subtitle_id, "language": language}
    save_state(video, subtitle, state)
    request_another(video, subtitle)
    return outcome


def request_another(video, subtitle):
    if queue_enabled():
        jobs.enqueue(jobs.KIND["blacklist"], {"video": video, "subtitle": subtitle}, delay_seconds=BLACKLIST_DELAY_SECONDS)
        log("bazarr.replacement-scheduled", f"blacklist job queued, delay={BLACKLIST_DELAY_SECONDS}s", video=video, subtitle=subtitle)
        return
    log_file = open(state_path("retries.log"), "a")
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (package_parent, os.environ.get("PYTHONPATH"))))}
    child = subprocess.Popen([sys.executable, "-m", "subtitle_guard", "try-another", video, subtitle], stdin=subprocess.DEVNULL,
                             stdout=log_file, stderr=log_file, start_new_session=True, env=environment)
    log("bazarr.replacement-spawned", f"try-another subprocess spawned, child_pid={child.pid}", video=video, subtitle=subtitle, child_pid=child.pid)


def queue_enabled():
    return os.environ.get("SUBTITLE_GUARD_QUEUE") == "1"


def scan_series(series_id, apply, queue=False):
    episodes = sorted((episode for episode in bazarr_get(f"/api/episodes?seriesid[]={series_id}")["data"] if episode.get("path")),
                      key=lambda item: (item["season"], item["episode"]))
    for position, episode in enumerate(episodes):
        nearby = episodes[max(0, position - NEIGHBOUR_EPISODES):position] + episodes[position + 1:position + NEIGHBOUR_EPISODES + 1]
        neighbours = [(f"S{other['season']:02d}E{other['episode']:02d}", other["path"]) for other in nearby]
        for subtitle in episode.get("subtitles", []):
            if subtitle.get("path"):
                label = f"S{episode['season']:02d}E{episode['episode']:02d}"
                scan_one(label, episode["path"], subtitle, apply, neighbours, queue)
    return 0


def scan_movies(apply, queue=False):
    for movie in bazarr_get("/api/movies?start=0&length=-1")["data"]:
        for subtitle in movie.get("subtitles", []):
            if movie.get("path") and subtitle.get("path"):
                scan_one(movie.get("title", "movie"), movie["path"], subtitle, apply, (), queue)
    return 0


def scan_one(label, video, subtitle, apply, neighbours, queue=False):
    if queue:
        job_id = jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle["path"], "apply": apply, "neighbours": list(neighbours)})
        print(f"{label} {subtitle['code2']}: queued job {job_id}", flush=True)
        return
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
    time.sleep(BLACKLIST_DELAY_SECONDS)
    retry = blacklist_for_retry(video, subtitle)
    if retry is None:
        return 0
    deadline = time.time() + RETRY_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(15)
        if another_arrived(video, subtitle, **retry):
            return 0
    finish_retry(video, subtitle, **retry)
    return 0


def blacklist_for_retry(video, subtitle):
    state = load_state(video, subtitle)
    pending = state.pop("pending_retry", None)
    save_state(video, subtitle, state)
    if not pending:
        return None
    form = {"provider": pending["provider"], "subs_id": pending["subtitle_id"], "language": pending["language"],
            "subtitles_path": pending["subtitle"]}
    if pending["media_type"] == "series":
        form.update(seriesid=pending["series_id"], episodeid=pending["media_id"])
        endpoint = "/api/episodes/blacklist"
    else:
        form.update(radarrid=pending["media_id"])
        endpoint = "/api/movies/blacklist"
    bazarr_post(endpoint, form)
    log("bazarr.blacklisted", f"subtitle blacklisted provider={pending['provider']} id={pending['subtitle_id']}", video=video, subtitle=subtitle,
        provider=pending["provider"], subtitle_id=pending["subtitle_id"], endpoint=endpoint)
    return {"pending": pending, "attempts_before": len(state["attempts"])}


def another_arrived(video, subtitle, pending, attempts_before):
    return len(load_state(video, subtitle).get("attempts", [])) > attempts_before or any_subtitle_for_language(pending)


def finish_retry(video, subtitle, pending, attempts_before):
    if another_arrived(video, subtitle, pending, attempts_before):
        return {"decision": "replaced", "summary": "Bazarr downloaded another subtitle"}
    restore_best(pending["subtitle"], load_state(video, subtitle))
    rescan(pending)
    log("bazarr.replacement-timeout", f"no replacement after {RETRY_WAIT_SECONDS}s, best attempt restored", LEVEL["warning"], video=video, subtitle=subtitle)
    return {"decision": DECISION["best_effort"], "summary": "no other subtitle arrived, kept the best version tried"}


def restore_best(subtitle, state):
    candidates = [attempt for attempt in state["attempts"] if attempt.get("subtitle") == subtitle and attempt.get("after")
                  and attempt.get("best_copy") and os.path.exists(attempt["best_copy"])]
    if not candidates:
        return
    best = max(candidates, key=lambda attempt: attempt["after"]["matched"])
    with subtitle_lock(subtitle):
        save_version(subtitle, backup_path(subtitle), "before-restore")
        restore_version(subtitle, best["best_copy"])
    report(subtitle, best.get("provider", ""), {**best, "decision": DECISION["best_effort"]})
    log("bazarr.best-attempt-restored", f"best attempt restored provider={best.get('provider')} matched={best['after']['matched']:.2f}", LEVEL["warning"],
        subtitle=subtitle, provider=best.get("provider"), matched=best["after"]["matched"])


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
    return json.load(call_bazarr(urllib.request.Request(BAZARR_URL + path, headers={"X-API-KEY": api_key()}), 60))


def bazarr_post(path, form):
    return bazarr_send(path, form, "POST")


def bazarr_patch(path, form):
    return bazarr_send(path, form, "PATCH")


def bazarr_send(path, form, method):
    request = urllib.request.Request(BAZARR_URL + path, data=urllib.parse.urlencode(form).encode(), method=method,
                                     headers={"X-API-KEY": api_key()})
    return call_bazarr(request, 900).status


def call_bazarr(request, timeout):
    started = time.time()
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except OSError as error:
        log("bazarr.api-error", f"{request.get_method()} {request.selector} failed: {error}", LEVEL["error"], method=request.get_method(),
            path=request.selector, error=str(error), seconds=round(time.time() - started, 2))
        raise
    log("bazarr.api-call", f"{request.get_method()} {request.selector} {response.status} in {time.time() - started:.2f}s", LEVEL["debug"],
        method=request.get_method(), path=request.selector, status=response.status, seconds=round(time.time() - started, 2))
    return response


def api_key():
    if os.environ.get("BAZARR_API_KEY"):
        return os.environ["BAZARR_API_KEY"]
    match = re.search(r"^auth:\n(?:[ \t]+.*\n)*?[ \t]+apikey: *(\S+)", open(BAZARR_CONFIG).read(), re.M)
    return match.group(1).strip("'\"")
