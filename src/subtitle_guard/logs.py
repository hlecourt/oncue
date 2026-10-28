"""Global log: one JSON event per line in the state directory, each with a level, the process that wrote it and a plain sentence."""

import json
import os
from collections import defaultdict

from .outcome import DECISION_WORDS
from .storage import now, state_path

MAX_LOG_BYTES = 10 * 1024 * 1024
TAIL_BYTES = 2 * 1024 * 1024
LEVEL = {"debug": "debug", "info": "info", "warning": "warning", "error": "error"}
settings = {"echo": False, "source": "cli"}
LEGACY_EVENTS = {
    "decision": ("guard", "info", "Checked: {decision}"),
    "job-started": ("job", "info", "Started task {job} ({kind})"),
    "job-done": ("job", "info", "Finished task {job}: {decision}"),
    "job-failed": ("job", "error", "Task {job} failed: {error}"),
    "supervisor-started": ("worker", "info", "Started the workers"),
    "role-restarted": ("worker", "warning", "{role} stopped, started it again"),
    "watch-started": ("watch", "info", "Started watching the library"),
    "watch-queued": ("watch", "info", "Found {jobs} new or changed subtitles, queued a check for each"),
    "manual-shift": ("review", "info", "Moved lines by {offset} s by hand"),
    "reverted": ("review", "info", "Put the original file back"),
    "accepted": ("review", "info", "Kept as it is by hand, it leaves the review list"),
    "moved-to-episode": ("review", "info", "Moved to {episode}"),
    "another-requested": ("review", "info", "Asked Bazarr for another subtitle instead of the {provider} one"),
    "blacklisted": ("bazarr", "info", "Told Bazarr not to use this {provider} subtitle again, waiting for another one"),
    "retry-scheduled": ("bazarr", "info", "Will ask Bazarr for another subtitle shortly"),
    "retry-started": ("bazarr", "info", "Asking Bazarr for another subtitle in the background"),
    "retry-gave-up": ("bazarr", "warning", "No other subtitle arrived, kept the best version tried"),
    "best-effort-restored": ("bazarr", "warning", "Put back the best version tried, from {provider}"),
}


def log(event, message, level=LEVEL["info"], **fields):
    line = json.dumps({"time": now(), "level": level, "source": settings["source"], "event": event, "message": message, **fields},
                      ensure_ascii=False, default=str)
    path = log_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
        os.replace(path, path + ".1")
    with open(path, "a") as handle:
        handle.write(line + "\n")
    if settings["echo"]:
        print(line, flush=True)


def recent_events():
    path = log_file()
    if not os.path.exists(path):
        return []
    start = max(0, os.path.getsize(path) - TAIL_BYTES)
    with open(path, "rb") as handle:
        handle.seek(start)
        lines = handle.read().decode(errors="replace").splitlines()
    events = []
    for line in reversed(lines[1:] if start else lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def event_level(event):
    return event.get("level") or LEGACY_EVENTS.get(event["event"], ("other", LEVEL["info"]))[1]


def event_message(event):
    if event.get("message"):
        return event["message"]
    if event["event"] not in LEGACY_EVENTS:
        return event["event"].replace("-", " ")
    values = {key: value for key, value in event.items() if value is not None}
    values["decision"] = DECISION_WORDS.get(event.get("decision"), event.get("decision") or "")
    return LEGACY_EVENTS[event["event"]][2].format_map(defaultdict(str, values))


def log_file():
    return state_path("subtitle-guard.log")
