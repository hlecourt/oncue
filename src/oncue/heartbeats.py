# SPDX-License-Identifier: GPL-3.0-or-later
"""Worker heartbeats: whether the workers of a running supervisor are alive, for /ready and for queueing retries from the UI."""

import json
import os
import time

from .storage import state_path

SUPERVISED_VARIABLE = "ONCUE_SUPERVISED"
HEARTBEAT_SECONDS = 10
HEARTBEAT_STALE_SECONDS = 60


def worker_health():
    supervisor = load_json(state_path("supervisor.json"))
    if not supervisor or not process_alive(supervisor["pid"]):
        return {}
    return {name: heartbeat_age(name) is not None and heartbeat_age(name) < HEARTBEAT_STALE_SECONDS for name in supervisor["workers"]}


def heartbeat_age(name):
    path = state_path("heartbeats", name)
    return time.time() - os.path.getmtime(path) if os.path.exists(path) else None


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return json.load(handle)
