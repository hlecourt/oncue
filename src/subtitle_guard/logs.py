"""Global log: one JSON event per line in the state directory, each with a level, the process that wrote it and a plain sentence."""

import json
import os

from .storage import now, state_path

MAX_LOG_BYTES = 10 * 1024 * 1024
TAIL_BYTES = 2 * 1024 * 1024
LEVEL = {"info": "info", "warning": "warning", "error": "error"}
settings = {"echo": False, "source": "cli"}


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


def log_file():
    return state_path("subtitle-guard.log")
