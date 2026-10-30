# SPDX-License-Identifier: GPL-3.0-or-later
"""The light copies the editor plays: one mono AAC file per video and audio track with its waveform peaks, made once by a job, and silent
windows of the picture, made on demand; all kept in a cache under a size cap that drops the least recently played first."""

import hashlib
import os
import tempfile
import threading
from contextlib import closing

import numpy

from . import jobs
from .errors import ProbeError
from .logs import log
from .reference import MEDIA_INPUT_OPTIONS, run_on_video
from .storage import state_path
from .tools import EXTRACT_SECONDS, run_tool, stderr_tail

COPY_VERSION = 1
PEAKS_PER_SECOND = 100
PEAK_SAMPLE_RATE = 4000
WINDOW_VERSION = 2
COPIED_WINDOW_SECONDS = 30
ENCODED_WINDOW_SECONDS = 10
WINDOW_HEIGHT = 720
WINDOW_ENCODING = ("-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p")
WINDOW_LOCK = threading.Lock()
CACHE_BYTES = int(os.environ.get("ONCUE_AUDIO_CACHE_MB", "2048")) * 1024 * 1024


def copy_paths(video, track):
    status = os.stat(video)
    key = hashlib.sha1(f"{COPY_VERSION}|{os.path.realpath(video)}|{status.st_size}|{status.st_mtime_ns}|{track}".encode()).hexdigest()
    folder = state_path("audio-copies")
    return os.path.join(folder, key + ".m4a"), os.path.join(folder, key + ".peaks")


def ready(video, track):
    audio, peaks = copy_paths(video, track)
    return os.path.exists(audio) and os.path.exists(peaks)


# a window to encode again is shorter, so its first picture shows sooner
def window_seconds(copyable):
    return COPIED_WINDOW_SECONDS if copyable else ENCODED_WINDOW_SECONDS


def window_path(video, index, seconds):
    status = os.stat(video)
    key = hashlib.sha1(f"{WINDOW_VERSION}|{os.path.realpath(video)}|{status.st_size}|{status.st_mtime_ns}|window|{seconds}|{index}".encode()).hexdigest()
    return os.path.join(state_path("audio-copies"), key + ".mp4")


# a failed copy is made again only on an explicit retry, so the editor's polling does not run ffmpeg in a loop
def request_copy(video, track, retry=False):
    if ready(video, track):
        return None, False
    with closing(jobs.connect()) as connection:
        latest = connection.execute("select id, status from jobs where kind = ? and json_extract(arguments, '$.video') = ? and json_extract(arguments, '$.track') = ? "
                                    "order by status in (?, ?) desc, id desc limit 1", (jobs.KIND["audio_copy"], video, track, jobs.STATUS["queued"],
                                                                                         jobs.STATUS["running"])).fetchone()
    if latest and latest["status"] in (jobs.STATUS["queued"], jobs.STATUS["running"]):
        return latest["id"], False
    if latest and latest["status"] == jobs.STATUS["failed"] and not retry:
        return None, True
    return jobs.enqueue(jobs.KIND["audio_copy"], {"video": video, "track": track}), False


def make_copy(video, track):
    audio, peaks = copy_paths(video, track)
    os.makedirs(os.path.dirname(audio), mode=0o700, exist_ok=True)
    descriptor, temporary_audio = tempfile.mkstemp(suffix=".m4a", dir=os.path.dirname(audio))
    os.close(descriptor)
    descriptor, temporary_pcm = tempfile.mkstemp(suffix=".pcm", dir=os.path.dirname(audio))
    os.close(descriptor)
    try:
        ffmpeg(["-i", video, "-map", f"0:{track}", "-vn", "-sn", "-dn", "-af", "aresample=async=1:first_pts=0", "-ac", "1", "-c:a", "aac", "-b:a", "48k",
                "-movflags", "+faststart", "-map_metadata", "-1", "-f", "mp4", "-y", temporary_audio], video)
        ffmpeg(["-i", temporary_audio, "-ac", "1", "-ar", str(PEAK_SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "s16le", "-y", temporary_pcm], video)
        samples = numpy.memmap(temporary_pcm, dtype="<i2", mode="r") if os.path.getsize(temporary_pcm) else numpy.zeros(0, dtype="<i2")
        bucket = PEAK_SAMPLE_RATE // PEAKS_PER_SECOND
        usable = len(samples) // bucket * bucket
        loudest = numpy.abs(samples[:usable].astype(numpy.int32)).reshape(-1, bucket).max(axis=1) if usable else numpy.zeros(0)
        numpy.minimum(loudest * 255 // 32768, 255).astype(numpy.uint8).tofile(temporary_pcm + ".peaks")
        os.replace(temporary_pcm + ".peaks", peaks)
        os.replace(temporary_audio, audio)
    finally:
        for leftover in (temporary_audio, temporary_pcm, temporary_pcm + ".peaks"):
            if os.path.exists(leftover):
                os.remove(leftover)
    log("audio-copy.made", f"audio copy of track {track} made, {os.path.getsize(audio) // 1024} KB", video=video, track=track)
    evict()
    return {"decision": "ready", "summary": f"audio copy of track {track} ready"}


# a picture browsers already play is copied as is, which takes a fraction of a second; any other one is encoded again
def video_window(video, index, copyable):
    target = window_path(video, index, window_seconds(copyable))
    with WINDOW_LOCK:
        if not os.path.exists(target):
            make_window(video, index, copyable, target)
    os.utime(target)
    return target


def make_window(video, index, copyable, target):
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(target))
    os.close(descriptor)
    seconds = window_seconds(copyable)
    source = ["-ss", str(index * seconds), "-i", video, "-t", str(seconds), "-map", "0:v:0", "-an", "-sn", "-dn"]
    output = ["-movflags", "+faststart", "-map_metadata", "-1", "-f", "mp4", "-y", temporary]
    try:
        if copyable:
            ffmpeg([*source, "-c:v", "copy", *output], video)
        else:
            encode_window(video, source, output)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    log("video-window.made", f"video window {index} made, {os.path.getsize(target) // 1024} KB", video=video, window=index, copied=copyable)
    evict()


def encode_window(video, source, output):
    ffmpeg(["-hwaccel", "auto", *source, "-vf", f"scale=-2:min({WINDOW_HEIGHT}\\,ih)", *WINDOW_ENCODING, *output], video, decoder_threads="0")


# a picture to encode again is decoded on every core, as a 4K video read on one core takes longer than the window lasts
def ffmpeg(arguments, video, decoder_threads="1"):
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *MEDIA_INPUT_OPTIONS, "-threads", decoder_threads, *arguments]
    result = run_on_video(command, video, EXTRACT_SECONDS) if video in arguments else run_tool(command, EXTRACT_SECONDS)
    if result.returncode != 0:
        raise ProbeError(f"ffmpeg could not make the light copy of {video}: {stderr_tail(result) or 'exit ' + str(result.returncode)}")


def played(video, track):
    audio, _ = copy_paths(video, track)
    os.utime(audio)
    return audio


def evict():
    folder = state_path("audio-copies")
    copies = sorted((os.path.getmtime(path), path) for path in (os.path.join(folder, name) for name in os.listdir(folder)) if path.endswith((".m4a", ".mp4")))
    total = sum(os.path.getsize(path) for _, path in copies)
    for _, path in copies[:-1]:
        if total <= CACHE_BYTES:
            break
        total -= os.path.getsize(path)
        for leftover in (path, path.removesuffix(".m4a") + ".peaks"):
            if os.path.exists(leftover):
                os.remove(leftover)
        log("audio-copy.evicted", f"light copy removed to stay under the cache cap: {path}", path=path)
