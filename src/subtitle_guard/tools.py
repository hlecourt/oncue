"""Run ffprobe, ffmpeg and ffsubsync with a time limit, so a hung tool cannot stall a worker forever."""

import subprocess

from .errors import ToolError

PROBE_SECONDS = 600
EXTRACT_SECONDS = 900
AUDIO_SYNC_SECONDS = 1800
STDERR_TAIL_CHARACTERS = 600


def run_tool(command, timeout):
    try:
        return subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise ToolError(f"{command[0]} gave no answer within {timeout}s") from error


def stderr_tail(result):
    return (result.stderr or "")[-STDERR_TAIL_CHARACTERS:].strip()
