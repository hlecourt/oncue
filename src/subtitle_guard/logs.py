"""Structured logs: one JSON event per line in the state directory, echoed to stdout by the worker."""

import json
import os

from .storage import now, state_path

MAX_LOG_BYTES = 10 * 1024 * 1024
settings = {"echo": False}


def log(event, **fields):
    line = json.dumps({"time": now(), "event": event, **fields}, ensure_ascii=False, default=str)
    path = log_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
        os.replace(path, path + ".1")
    with open(path, "a") as handle:
        handle.write(line + "\n")
    if settings["echo"]:
        print(line, flush=True)


def log_file():
    return state_path("subtitle-guard.log")
