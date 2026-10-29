# SPDX-License-Identifier: GPL-3.0-or-later
"""Fallback for videos without an embedded track: timings from ffsubsync, turned into a proposal by the guard."""

import itertools
import json
import os
import shutil
import sys
import tempfile

import numpy

from .logs import LEVEL, log
from .outcome import DECISION
from .storage import heavy_disk_read, state_path
from .tools import AUDIO_SYNC_SECONDS, run_tool, stderr_tail

FFSUBSYNC_LIBRARIES = os.environ.get("SUBTITLE_GUARD_FFSUBSYNC_PATH", "/app/bazarr/bin/libs")
AUDIO_SYNC_MAX_OFFSET_SECONDS = 120
AUDIO_SYNC_EDGE_SHARE = 0.9
AUDIO_SYNC_PLAIN_MAX_OFFSET_SECONDS = 30
AUDIO_SYNC_FORMATS = (".srt", ".ass", ".ssa", ".vtt")
AUDIO_SYNC_SCRIPT = ("import json, sys\nsys.path.insert(0, sys.argv.pop(1))\nfrom ffsubsync.ffsubsync import make_parser, run\n"
                     "result = run(make_parser().parse_args(sys.argv[1:]))\n"
                     "print(json.dumps({key: result.get(key) for key in ('offset_seconds', 'framerate_scale_factor', 'sync_was_successful')}))")
SPEECH_SCRIPT = ("import sys\nsys.path.insert(0, sys.argv.pop(1))\nimport numpy\nfrom ffsubsync.speech_transformers import VideoSpeechTransformer\n"
                 "mask = VideoSpeechTransformer(vad='webrtc', sample_rate=100, frame_rate=48000, non_speech_label=0.0).fit(sys.argv[1]).transform()\n"
                 "numpy.save(sys.argv[2], numpy.asarray(mask) > 0.5)")
SPEECH_FRAMES_PER_SECOND = 100
PART_MIN_LINES = 20
MAX_PARTS = 8
MIN_PARTS = 4
PART_SEARCH_SECONDS = 3.0
PART_AGREEMENT_SECONDS = 0.3
PART_MIN_LIFT = 4.0
CHANCE_LAGS = numpy.arange(10 * SPEECH_FRAMES_PER_SECOND, 60 * SPEECH_FRAMES_PER_SECOND, 25)
ARGUMENTS_CHECK_SCRIPT = ("import sys\nsys.path.insert(0, sys.argv.pop(1))\nfrom ffsubsync.ffsubsync import make_parser\nmake_parser().parse_args(sys.argv[1:])")


def audio_fix(video, subtitle, document):
    extension = os.path.splitext(subtitle)[1].lower()
    if extension not in AUDIO_SYNC_FORMATS:
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, format not supported by ffsubsync"}, None
    work = state_path("audio-work")
    os.makedirs(work, mode=0o700, exist_ok=True)
    descriptor, output = tempfile.mkstemp(prefix="audio-", suffix=extension, dir=work)
    os.close(descriptor)
    try:
        return synced_timings(video, subtitle, document, output)
    finally:
        if os.path.exists(output):
            os.remove(output)


def synced_timings(video, subtitle, document, output):
    result = run_ffsubsync(video, subtitle, output, fix_framerate=True)
    if result and abs(result["offset_seconds"]) >= AUDIO_SYNC_EDGE_SHARE * AUDIO_SYNC_MAX_OFFSET_SECONDS:
        result = run_ffsubsync(video, subtitle, output, fix_framerate=False)
        if result and abs(result["offset_seconds"]) > AUDIO_SYNC_PLAIN_MAX_OFFSET_SECONDS:
            result = None
    if not result:
        log("audio.sync-rejected", "ffsubsync gave no usable result (failed or offset beyond limit), file unchanged", LEVEL["warning"], subtitle=subtitle)
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, audio sync not confident"}, None
    scale, offset = result["framerate_scale_factor"], result["offset_seconds"]
    timings = [(start * scale + offset, end * scale + offset, text) for start, end, text in document["cues"]]
    agreement = part_agreement(timings, speech_mask(video, subtitle))
    starts = [start for start, _, _ in document["cues"]]
    log("audio.synced", f"ffsubsync found offset={result['offset_seconds']:+.2f}s scale={result['framerate_scale_factor']:.4f}", subtitle=subtitle, **result)
    moved = [{"first": starts[0], "last": starts[-1], "lines": len(starts), "shift": round(result["offset_seconds"], 2)}] if starts else []
    return ({"decision": DECISION["audio_synced"], "reason": f"synced to the audio by ffsubsync, offset {result['offset_seconds']:+.2f}s, "
                                                             f"scale {result['framerate_scale_factor']:.4f}, not verified",
             "changes": {"scale": result["framerate_scale_factor"], "lines": len(starts), "parts": moved}, "agreement": agreement}, timings)


def speech_mask(video, subtitle):
    work = state_path("audio-work")
    os.makedirs(work, mode=0o700, exist_ok=True)
    descriptor, output = tempfile.mkstemp(prefix="speech-", suffix=".npy", dir=work)
    os.close(descriptor)
    try:
        with heavy_disk_read():
            finished = run_tool([sys.executable, "-c", SPEECH_SCRIPT, FFSUBSYNC_LIBRARIES, video, output], AUDIO_SYNC_SECONDS)
        if finished.returncode != 0:
            log("audio.speech-not-detected", f"speech detection failed exit={finished.returncode}", LEVEL["warning"], subtitle=subtitle, stderr=stderr_tail(finished))
            return None
        return numpy.load(output, allow_pickle=False)
    finally:
        os.remove(output)


def part_agreement(timings, speech):
    parts = min(MAX_PARTS, len(timings) // PART_MIN_LINES)
    if speech is None or parts < MIN_PARTS:
        return {"parts": parts, "agreeing": 0, "residuals": [], "lifts": []}
    ordered = sorted((start, end) for start, end, _ in timings)
    bounds = numpy.linspace(0, len(ordered), parts + 1).astype(int)
    cumulative = numpy.concatenate([[0.0], numpy.cumsum(speech.astype(numpy.float32) * 2 - 1)])
    measures = [part_measure(ordered[first:last], cumulative) for first, last in itertools.pairwise(bounds)]
    residuals, lifts = [residual for residual, _ in measures], [lift for _, lift in measures]
    agreeing = sum(abs(residual) <= PART_AGREEMENT_SECONDS and lift >= PART_MIN_LIFT for residual, lift in measures)
    return {"parts": parts, "agreeing": agreeing, "residuals": residuals, "lifts": lifts}


def part_measure(cues, cumulative):
    frames = (numpy.array(cues) * SPEECH_FRAMES_PER_SECOND).astype(int)
    total = max(int((frames[:, 1] - frames[:, 0]).sum()), 1)

    def speech_share(lags):
        ends = numpy.clip(frames[:, 1:2] + lags, 0, len(cumulative) - 1)
        starts = numpy.clip(frames[:, 0:1] + lags, 0, len(cumulative) - 1)
        return (cumulative[ends] - cumulative[starts]).sum(axis=0) / total
    search = int(PART_SEARCH_SECONDS * SPEECH_FRAMES_PER_SECOND)
    near = numpy.arange(-search, search + 1)
    residual = round(float(near[int(numpy.argmax(speech_share(near)))]) / SPEECH_FRAMES_PER_SECOND, 2)
    far = numpy.concatenate([-CHANCE_LAGS[::-1], CHANCE_LAGS])
    chance = speech_share(far)
    lift = (float(speech_share(numpy.array([0]))[0]) - float(numpy.median(chance))) / (float(chance.std()) + 1e-9)
    return residual, round(lift, 2)


def ffsubsync_arguments(video, subtitle, output, fix_framerate):
    arguments = [video, "-i", subtitle, "-o", output, "--ffmpegpath", os.path.dirname(shutil.which("ffmpeg") or "/usr/bin/ffmpeg"), "--vad", "webrtc",
                 "--max-offset-seconds", str(AUDIO_SYNC_MAX_OFFSET_SECONDS), "--skip-sync-on-low-quality",
                 "--quality-max-offset-seconds", str(AUDIO_SYNC_MAX_OFFSET_SECONDS), "--max-framerate-deviation", "0.05", "--output-encoding", "same"]
    return arguments if fix_framerate else [*arguments, "--no-fix-framerate"]


def run_ffsubsync(video, subtitle, output, fix_framerate):
    command = [sys.executable, "-c", AUDIO_SYNC_SCRIPT, FFSUBSYNC_LIBRARIES, *ffsubsync_arguments(video, subtitle, output, fix_framerate)]
    with heavy_disk_read():
        finished = run_tool(command, AUDIO_SYNC_SECONDS)
    lines = finished.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        result = {}
    if finished.returncode != 0 or not result.get("sync_was_successful") or not os.path.exists(output):
        log("audio.ffsubsync-failed", f"ffsubsync exit={finished.returncode} successful={result.get('sync_was_successful')}", LEVEL["warning"], subtitle=subtitle,
            stderr=stderr_tail(finished))
        return None
    return {"offset_seconds": result.get("offset_seconds") or 0.0, "framerate_scale_factor": result.get("framerate_scale_factor") or 1.0}
