# SPDX-License-Identifier: GPL-3.0-or-later
"""Run ffprobe, ffmpeg and ffsubsync with a time limit, in their own process group, so neither a hung tool nor its children outlive the job."""

import json
import os
import signal
import subprocess

from .errors import ToolError
from .logs import LEVEL, log, settings
from .storage import state_path

PROBE_SECONDS = 600
EXTRACT_SECONDS = 900
AUDIO_SYNC_SECONDS = 1800
STDERR_TAIL_CHARACTERS = 600
ACTIVE_GROUPS = set()


def run_tool(command, timeout):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True, errors="replace",
                               start_new_session=True)
    remember_group(process.pid)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        kill_group(process.pid)
        process.communicate()
        raise ToolError(f"{command[0]} gave no answer within {timeout}s") from error
    finally:
        forget_group(process.pid)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def stderr_tail(result):
    return (result.stderr or "")[-STDERR_TAIL_CHARACTERS:].strip()


def kill_group(group, number=signal.SIGKILL):
    try:
        os.killpg(group, number)
    except (ProcessLookupError, PermissionError):
        return


def kill_active_groups():
    for group in list(ACTIVE_GROUPS):
        kill_group(group)


def kill_recorded_groups(role):
    path = groups_file(role)
    if not os.path.exists(path):
        return
    with open(path) as handle:
        groups = json.load(handle)
    for group in groups:
        kill_group(group)
    os.remove(path)


def remember_group(group):
    ACTIVE_GROUPS.add(group)
    save_groups()


def forget_group(group):
    ACTIVE_GROUPS.discard(group)
    save_groups()


def save_groups():
    path = groups_file(settings["source"])
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump(sorted(ACTIVE_GROUPS), handle)
    except OSError as error:
        log("tool.groups-not-saved", f"running tools not recorded in {path}, they cannot be cleaned up after a crash: {error}", LEVEL["warning"], path=path)


def groups_file(role):
    return state_path("tool-groups", f"{role}.json")
