# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-subtitle state kept between checks: the attempts Bazarr made and the retry waiting to be sent."""

import hashlib
import os

from .logs import LEVEL, log
from .storage import read_json, state_path, write_json


def load_state(video, subtitle):
    path = state_file(video, subtitle)
    if not os.path.exists(path):
        return {}
    try:
        return read_json(path)
    except ValueError as error:
        os.replace(path, path + ".corrupt")
        log("state.corrupt", f"unreadable state set aside as {path}.corrupt, starting empty: {error}", LEVEL["warning"], video=video, subtitle=subtitle)
        return {}


def save_state(video, subtitle, state):
    write_json(state_file(video, subtitle), state)


def state_file(video, subtitle):
    stem, name = os.path.splitext(os.path.basename(video))[0], os.path.basename(subtitle)
    suffix = name.removeprefix(stem)
    return state_path("state", hashlib.sha1(f"{video}|{suffix}".encode()).hexdigest() + ".json")
