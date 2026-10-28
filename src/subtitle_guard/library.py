"""Standalone mode: find videos and their external subtitles in media folders, without Bazarr."""

import os
import re
import time
from datetime import datetime

from . import jobs
from .guard import evaluate_and_fix
from .logs import log, settings
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
WATCH_SECONDS = int(os.environ.get("SUBTITLE_GUARD_WATCH_SECONDS", "600"))


def scan_folder(root, apply, queue=False):
    for video, subtitle, neighbours in library_items(root):
        label = os.path.relpath(subtitle, root)
        if queue:
            job_id = jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle, "apply": apply, "neighbours": neighbours})
            print(f"{label}: queued job {job_id}", flush=True)
            continue
        try:
            outcome = evaluate_and_fix(video, subtitle, apply, neighbours)
        except (OSError, ValueError) as error:
            print(f"{label}: error {error}", flush=True)
            continue
        report(subtitle, "scan", outcome, applied=apply)
        print(f"{label}: {describe(outcome)}", flush=True)
    return 0


def watch(roots, apply):
    settings["source"] = "watch"
    log("watch.started", f"watching {len(roots)} roots interval={WATCH_SECONDS}s apply={apply}", roots=roots, applied=apply, interval=WATCH_SECONDS)
    while True:
        queued = 0
        for root in roots:
            for video, subtitle, neighbours in library_items(root):
                queued += queue_if_changed(video, subtitle, neighbours, apply)
        if queued:
            log("watch.changes-queued", f"{queued} new or changed subtitles queued", jobs=queued)
        time.sleep(WATCH_SECONDS)


def queue_if_changed(video, subtitle, neighbours, apply):
    try:
        signature = watch_signature(video, subtitle, apply)
    except FileNotFoundError:
        return 0
    remembered, updated = jobs.seen(subtitle)
    if remembered in (signature, PENDING + signature):
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
        names = sorted(files)
        videos = [name for name in names if name.lower().endswith(VIDEO_EXTENSIONS) and not SAMPLE.search(os.path.splitext(name)[0])]
        if videos:
            yield folder, videos, names


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
