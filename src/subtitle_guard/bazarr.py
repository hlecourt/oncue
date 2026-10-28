# SPDX-License-Identifier: GPL-3.0-or-later
"""Bazarr integration: post-processing hook, retries through the blacklist, and library scans."""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import jobs
from .errors import ConfigError, SubtitleGuardError
from .files import read_regular, restore_version, save_version, subtitle_lock
from .guard import evaluate_and_fix
from .library import nearby
from .logs import LEVEL, log
from .outcome import DECISION, describe
from .reports import report
from .state import load_state, update_state
from .storage import backup_path, now, state_path

BAZARR_URL = os.environ.get("BAZARR_URL", "http://localhost:6767")
BAZARR_CONFIG = os.environ.get("BAZARR_CONFIG", "/config/config/config.yaml")
MAX_SUBTITLES_PER_MEDIA = 4
RETRY_WAIT_SECONDS = 900
BLACKLIST_DELAY_SECONDS = 20
API_KEY_HEADER = "X-API-KEY"
RETRY_STAGE = {"requested": "requested", "blacklisted": "blacklisted"}


def handle_download(video, subtitle, language, provider, subtitle_id, media_id, series_id="", content_hash=None, queued=False):
    try:
        received = file_hash(subtitle)
    except SubtitleGuardError as error:
        return {"decision": DECISION["unverified"], "reason": f"refused: {error}"}
    if content_hash and content_hash != received:
        log("bazarr.stale-hook", "subtitle replaced since this hook was queued, skipped; the newer download has its own hook", LEVEL["warning"],
            subtitle=subtitle, provider=provider, subtitle_id=subtitle_id)
        return {"decision": "skipped", "summary": "the subtitle changed since it was queued, the newer download is checked on its own"}
    series_number = int(series_id) if series_id.isdigit() else None
    media_number = int(media_id) if media_id.isdigit() else None
    neighbours = series_neighbours(series_number, video) if series_number else ()
    outcome = evaluate_and_fix(video, subtitle, apply=True, neighbours=neighbours)
    attempt = {"time": now(), "provider": provider, "subtitle_id": subtitle_id, "subtitle": subtitle, "language": language, "media_id": media_number,
               "series_id": series_number, "content_hash": received, **outcome}

    def record(state):
        if state.get("video_size") != os.path.getsize(video):
            state.clear()
            state.update(video_size=os.path.getsize(video), attempts=[])
        state.setdefault("attempts", []).append(attempt)
        if outcome["decision"] != DECISION["rejected"]:
            state.pop("pending_retry", None)

    state = update_state(video, subtitle, record)
    report(subtitle, provider, outcome)
    if outcome["decision"] != DECISION["rejected"] or media_number is None:
        return outcome
    if len(state["attempts"]) >= MAX_SUBTITLES_PER_MEDIA:
        restore_best(subtitle, state)
        return outcome
    pending = {"media_type": "series" if series_number else "movie", "media_id": media_number, "series_id": series_number, "subtitle": subtitle,
               "provider": provider, "subtitle_id": subtitle_id, "language": language, "content_hash": received, "stage": RETRY_STAGE["requested"]}
    update_state(video, subtitle, lambda state: state.update(pending_retry=pending))
    request_another(video, subtitle, queued)
    return outcome


def file_hash(path):
    return hashlib.sha1(read_regular(path)).hexdigest()


def request_another(video, subtitle, queued=False):
    if queued or queue_enabled():
        jobs.enqueue(jobs.KIND["blacklist"], {"video": video, "subtitle": subtitle}, delay_seconds=BLACKLIST_DELAY_SECONDS)
        log("bazarr.replacement-scheduled", f"blacklist job queued, delay={BLACKLIST_DELAY_SECONDS}s", video=video, subtitle=subtitle)
        return
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (package_parent, os.environ.get("PYTHONPATH"))))}
    with open(state_path("retries.log"), "a") as retries_log:
        child = subprocess.Popen([sys.executable, "-m", "subtitle_guard", "try-another", video, subtitle], stdin=subprocess.DEVNULL,
                                 stdout=retries_log, stderr=retries_log, start_new_session=True, env=environment)
    log("bazarr.replacement-spawned", f"try-another subprocess spawned, child_pid={child.pid}", video=video, subtitle=subtitle, child_pid=child.pid)


def queue_enabled():
    return os.environ.get("SUBTITLE_GUARD_QUEUE") == "1"


def scan_series(series_id, apply, queue=False):
    episodes = sorted((episode for episode in bazarr_get(f"/api/episodes?seriesid[]={series_id}")["data"] if episode.get("path")),
                      key=lambda item: (item["season"], item["episode"]))
    for position, episode in enumerate(episodes):
        neighbours = [(f"S{other['season']:02d}E{other['episode']:02d}", other["path"]) for other in nearby(episodes, position)]
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
    except (OSError, ValueError, SubtitleGuardError) as error:
        print(f"{label} {subtitle['code2']}: error {error}", flush=True)
        return
    report(subtitle["path"], "scan", outcome, applied=apply)
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
    return [(f"S{episode['season']:02d}E{episode['episode']:02d}", episode["path"]) for episode in nearby(episodes, position)]


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
    pending = state.get("pending_retry")
    if not pending:
        return None
    if replaced_since(subtitle, pending):
        update_state(video, subtitle, lambda current: current.pop("pending_retry", None))
        log("bazarr.replacement-cancelled", "the rejected subtitle was replaced before the blacklist, nothing sent to Bazarr", LEVEL["info"], video=video,
            subtitle=subtitle, provider=pending["provider"], subtitle_id=pending["subtitle_id"])
        return None
    if pending.get("stage") == RETRY_STAGE["blacklisted"]:
        return {"pending": pending, "attempts_before": len(state.get("attempts", []))}
    form = {"provider": pending["provider"], "subs_id": pending["subtitle_id"], "language": pending["language"],
            "subtitles_path": pending["subtitle"]}
    if pending["media_type"] == "series":
        form.update(seriesid=pending["series_id"], episodeid=pending["media_id"])
        endpoint = "/api/episodes/blacklist"
    else:
        form.update(radarrid=pending["media_id"])
        endpoint = "/api/movies/blacklist"
    bazarr_post(endpoint, form)
    pending = {**pending, "stage": RETRY_STAGE["blacklisted"]}
    update_state(video, subtitle, lambda current: current.update(pending_retry=pending))
    log("bazarr.blacklisted", f"subtitle blacklisted provider={pending['provider']} id={pending['subtitle_id']}", video=video, subtitle=subtitle,
        provider=pending["provider"], subtitle_id=pending["subtitle_id"], endpoint=endpoint)
    return {"pending": pending, "attempts_before": len(state.get("attempts", []))}


def replaced_since(subtitle, pending):
    return bool(pending.get("content_hash")) and os.path.isfile(subtitle) and file_hash(subtitle) != pending["content_hash"]


def another_arrived(video, subtitle, pending, attempts_before):
    return len(load_state(video, subtitle).get("attempts", [])) > attempts_before or replaced_since(subtitle, pending)


def finish_retry(video, subtitle, pending, attempts_before):
    if another_arrived(video, subtitle, pending, attempts_before):
        update_state(video, subtitle, lambda state: state.pop("pending_retry", None))
        return {"decision": "replaced", "summary": "Bazarr downloaded another subtitle"}
    restore_best(pending["subtitle"], load_state(video, subtitle))
    rescan(pending)
    update_state(video, subtitle, lambda state: state.pop("pending_retry", None))
    log("bazarr.replacement-timeout", f"no replacement after {RETRY_WAIT_SECONDS}s, best attempt restored", LEVEL["warning"], video=video, subtitle=subtitle)
    return {"decision": DECISION["best_effort"], "summary": "no other subtitle arrived, kept the best version tried"}


def restore_best(subtitle, state):
    candidates = [attempt for attempt in state["attempts"] if attempt.get("subtitle") == subtitle and attempt.get("after")
                  and attempt.get("best_copy") and os.path.exists(attempt["best_copy"])]
    if not candidates:
        return
    best = max(candidates, key=lambda attempt: attempt["after"]["matched"])
    with subtitle_lock(subtitle):
        if os.path.exists(subtitle):
            save_version(subtitle, backup_path(subtitle), "before-restore")
        restore_version(subtitle, best["best_copy"])
    report(subtitle, best.get("provider", ""), {**best, "decision": DECISION["best_effort"]})
    log("bazarr.best-attempt-restored", f"best attempt restored provider={best.get('provider')} matched={best['after']['matched']:.2f}", LEVEL["warning"],
        subtitle=subtitle, provider=best.get("provider"), matched=best["after"]["matched"])


def rescan(pending):
    if pending["media_type"] == "series":
        bazarr_patch("/api/series", {"seriesid": pending["series_id"], "action": "scan-disk"})
        return
    bazarr_patch("/api/movies", {"radarrid": pending["media_id"], "action": "scan-disk"})


def bazarr_get(path):
    with call_bazarr(urllib.request.Request(BAZARR_URL + path, headers={API_KEY_HEADER: api_key()}), 60) as response:
        return json.load(response)


def bazarr_post(path, form):
    return bazarr_send(path, form, "POST")


def bazarr_patch(path, form):
    return bazarr_send(path, form, "PATCH")


def bazarr_send(path, form, method):
    request = urllib.request.Request(BAZARR_URL + path, data=urllib.parse.urlencode(form).encode(), method=method,
                                     headers={API_KEY_HEADER: api_key()})
    with call_bazarr(request, 900) as response:
        return response.status


def call_bazarr(request, timeout):
    started = time.time()
    try:
        response = OPENER.open(request, timeout=timeout)
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
    with open(BAZARR_CONFIG) as handle:
        match = re.search(r"^auth:\n(?:[ \t]+.*\n)*?[ \t]+apikey: *(\S+)", handle.read(), re.MULTILINE)
    if match is None:
        raise ConfigError(f"no auth.apikey in {BAZARR_CONFIG}, set BAZARR_API_KEY")
    return match.group(1).strip("'\"")


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if origin(new_url) != origin(request.full_url):
            raise urllib.error.HTTPError(request.full_url, code, f"refused a redirect to another origin: {new_url}", headers, response)
        return super().redirect_request(request, response, code, message, headers, new_url)


def origin(url):
    parts = urllib.parse.urlparse(url)
    return parts.scheme, parts.hostname, parts.port or {"http": 80, "https": 443}.get(parts.scheme)


OPENER = urllib.request.build_opener(SameOriginRedirect)
