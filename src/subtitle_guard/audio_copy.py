# SPDX-License-Identifier: GPL-3.0-or-later
"""The light audio copy the editor plays: one mono AAC file per video and audio track, made once by a job, with its waveform peaks, kept in a
cache under a size cap that drops the least recently played copies first."""

import hashlib
import os
import tempfile
from contextlib import closing

import numpy

from . import jobs
from .errors import ProbeError
from .logs import log
from .storage import state_path
from .tools import EXTRACT_SECONDS, run_tool, stderr_tail

COPY_VERSION = 1
PEAKS_PER_SECOND = 100
PEAK_SAMPLE_RATE = 4000
CACHE_BYTES = int(os.environ.get("SUBTITLE_GUARD_AUDIO_CACHE_MB", "2048")) * 1024 * 1024


def copy_paths(video, track):
    status = os.stat(video)
    key = hashlib.sha1(f"{COPY_VERSION}|{os.path.realpath(video)}|{status.st_size}|{status.st_mtime_ns}|{track}".encode()).hexdigest()
    folder = state_path("audio-copies")
    return os.path.join(folder, key + ".m4a"), os.path.join(folder, key + ".peaks")


def ready(video, track):
    audio, peaks = copy_paths(video, track)
    return os.path.exists(audio) and os.path.exists(peaks)


def request_copy(video, track):
    if ready(video, track):
        return None
    with closing(jobs.connect()) as connection:
        waiting = connection.execute("select id from jobs where kind = ? and status in (?, ?) and json_extract(arguments, '$.video') = ? "
                                     "and json_extract(arguments, '$.track') = ?", (jobs.KIND["audio_copy"], jobs.STATUS["queued"], jobs.STATUS["running"],
                                                                                    video, track)).fetchone()
    return waiting["id"] if waiting else jobs.enqueue(jobs.KIND["audio_copy"], {"video": video, "track": track})


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


def ffmpeg(arguments, video):
    result = run_tool(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file", "-threads", "1", *arguments], EXTRACT_SECONDS)
    if result.returncode != 0:
        raise ProbeError(f"ffmpeg could not make the audio copy of {video}: {stderr_tail(result) or 'exit ' + str(result.returncode)}")


def played(video, track):
    audio, _ = copy_paths(video, track)
    os.utime(audio)
    return audio


def evict():
    folder = state_path("audio-copies")
    copies = sorted((os.path.getmtime(path), path) for path in (os.path.join(folder, name) for name in os.listdir(folder)) if path.endswith(".m4a"))
    total = sum(os.path.getsize(path) for _, path in copies)
    for _, path in copies[:-1]:
        if total <= CACHE_BYTES:
            break
        total -= os.path.getsize(path)
        for leftover in (path, path[: -len(".m4a")] + ".peaks"):
            if os.path.exists(leftover):
                os.remove(leftover)
        log("audio-copy.evicted", f"audio copy removed to stay under the cache cap: {path}", path=path)
