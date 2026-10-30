# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone mode: find videos and their external subtitles in media folders, without Bazarr."""

import os
import re
import time
from contextlib import suppress
from datetime import datetime

from . import configuration, jobs
from .errors import SubtitleGuardError, UnsafePathError
from .files import TEMPORARY_SUFFIX, remove_file
from .guard import evaluate_and_fix
from .logs import LEVEL, log, settings
from .outcome import describe
from .reports import report
from .storage import file_signature

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm")
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt")
EPISODE = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})|(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)")
SAMPLE = re.compile(r"(^|[ ._-])sample([ ._-]|$)", re.IGNORECASE)
NEIGHBOUR_EPISODES = 2
PENDING = "pending:"
FAILED = "failed:"
FAILED_RETRY_SECONDS = 3600
LEFTOVER_SECONDS = 3600


def scan_folder(root, apply, queue=False):
    for video, subtitle, neighbours in library_items(root):
        label = os.path.relpath(subtitle, root)
        if queue:
            job_id = jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle, "apply": apply, "neighbours": neighbours})
            print(f"{label}: queued job {job_id}", flush=True)
            continue
        try:
            outcome = evaluate_and_fix(video, subtitle, apply, neighbours)
        except (OSError, ValueError, SubtitleGuardError) as error:
            print(f"{label}: error {error}", flush=True)
            continue
        report(subtitle, "scan", outcome, applied=apply)
        print(f"{label}: {describe(outcome)}", flush=True)
    return 0


def watch(folders, apply):
    settings["source"] = "watch"
    log("watch.started", f"watching {len(folders)} folders interval={configuration.value('watch_seconds')}s apply={apply}", folders=folders, applied=apply,
        interval=configuration.value("watch_seconds"))
    while True:
        queued = 0
        for folder in folders:
            for video, subtitle, neighbours in library_items(folder):
                queued += queue_if_changed(video, subtitle, neighbours, apply)
        if queued:
            log("watch.changes-queued", f"{queued} new or changed subtitles queued", jobs=queued)
        time.sleep(configuration.value("watch_seconds"))


def queue_if_changed(video, subtitle, neighbours, apply):
    try:
        signature = watch_signature(video, subtitle, apply)
    except FileNotFoundError:
        return 0
    remembered, updated = jobs.seen(subtitle)
    if remembered == signature or (remembered == PENDING + signature and jobs.has_open_job(subtitle)):
        return 0
    if remembered == FAILED + signature and time.time() - datetime.fromisoformat(updated).timestamp() < FAILED_RETRY_SECONDS:
        return 0
    jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle, "apply": apply, "neighbours": neighbours, "watched": True})
    jobs.remember(subtitle, PENDING + signature)
    return 1


def watch_signature(video, subtitle, apply):
    return f"{file_signature(subtitle)}|{file_signature(video)}|{int(apply)}"


def library_items(root):
    for folder, videos, names in media_folders(root):
        for video in videos:
            for subtitle in external_subtitles(video, names):
                yield os.path.join(folder, video), os.path.join(folder, subtitle), folder_neighbours(folder, video, videos)


def media_folders(root):
    for folder, directories, files in os.walk(root):
        directories[:] = sorted(directory for directory in directories if not directory.startswith("."))
        names = sorted(name for name in files if not leftover_temporary(folder, name))
        videos = [name for name in names if name.lower().endswith(VIDEO_EXTENSIONS) and not SAMPLE.search(os.path.splitext(name)[0])]
        if videos:
            yield folder, videos, names


def leftover_temporary(folder, name):
    if not name.endswith(TEMPORARY_SUFFIX):
        return False
    path = os.path.join(folder, name)
    # remove_file refuses outside SUBTITLE_GUARD_ROOTS (or without it): such a leftover is only skipped
    with suppress(FileNotFoundError, UnsafePathError):
        if time.time() - os.path.getmtime(path) > LEFTOVER_SECONDS:
            remove_file(path)
            log("watch.leftover-removed", f"removed a temporary file left by an interrupted write: {path}", LEVEL["warning"], file=path)
    return True


def external_subtitles(video, names):
    stem = os.path.splitext(video)[0]
    return [name for name in names if name.startswith(stem + ".") and name.lower().endswith(SUBTITLE_EXTENSIONS) and ".subtitle-guard" not in name]


def folder_neighbours(folder, video, videos):
    episodes = sorted((episode_key(name), name) for name in videos if episode_key(name))
    names = [name for _, name in episodes]
    if video not in names:
        return []
    return [[label(name), os.path.join(folder, name)] for name in nearby(names, names.index(video))]


def nearby(items, position):
    return items[max(0, position - NEIGHBOUR_EPISODES):position] + items[position + 1:position + NEIGHBOUR_EPISODES + 1]


def episode_key(name):
    match = EPISODE.search(name)
    if not match:
        return None
    season, episode = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
    return int(season), int(episode)


def label(name):
    season, episode = episode_key(name)
    return f"S{season:02d}E{episode:02d}"
