# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-subtitle state kept between checks: the attempts Bazarr made and the replacement waiting to be sent."""

import fcntl
import hashlib
import os
from contextlib import contextmanager

from .logs import LEVEL, log
from .storage import read_json, state_path, write_json

RENAMED_KEYS = {"pending_retry": "pending_replacement"}


def load_state(video, subtitle):
    path = state_file(video, subtitle)
    if not os.path.exists(path):
        return {}
    try:
        state = read_json(path)
    except ValueError as error:
        os.replace(path, path + ".corrupt")
        log("state.corrupt", f"unreadable state set aside as {path}.corrupt, starting empty: {error}", LEVEL["warning"], video=video, subtitle=subtitle)
        return {}
    for old, new in RENAMED_KEYS.items():
        if old in state:
            state[new] = state.pop(old)
    return state


def save_state(video, subtitle, state):
    write_json(state_file(video, subtitle), state)


def state_file(video, subtitle):
    stem, name = os.path.splitext(os.path.basename(video))[0], os.path.basename(subtitle)
    suffix = name.removeprefix(stem)
    return state_path("state", hashlib.sha1(f"{video}|{suffix}".encode()).hexdigest() + ".json")


@contextmanager
def state_lock(video, subtitle):
    lock_file = state_file(video, subtitle) + ".lock"
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    with open(lock_file, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def update_state(video, subtitle, change):
    with state_lock(video, subtitle):
        state = load_state(video, subtitle)
        change(state)
        save_state(video, subtitle, state)
        return state
