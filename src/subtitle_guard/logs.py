"""Structured logs: one JSON event per line in the state directory, echoed to stdout by the worker."""

import json
import os

from .storage import STATE_DIRECTORY, now

LOG_FILE = os.path.join(STATE_DIRECTORY, "subtitle-guard.log")
MAX_LOG_BYTES = 10 * 1024 * 1024
settings = {"echo": False}


def log(event, **fields):
    line = json.dumps({"time": now(), "event": event, **fields}, ensure_ascii=False, default=str)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
        os.replace(LOG_FILE, LOG_FILE + ".1")
    with open(LOG_FILE, "a") as handle:
        handle.write(line + "\n")
    if settings["echo"]:
        print(line, flush=True)
