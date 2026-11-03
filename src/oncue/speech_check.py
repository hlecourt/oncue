# SPDX-License-Identifier: GPL-3.0-or-later
"""The speech check: how far a set of timed lines sits from the speech of the video, for the subtitle and for the reference alike, since a reference track can lead the speech too.

A speech onset starts a run of speech after MIN_SILENCE of silence. The offset of a set of line starts is the peak of the histogram of every line-onset lag within MAX_LAG, smoothed at KERNEL_SIGMA, less the
BASELINE measured on official tracks: chance pairs make a flat floor, the true offset a bump, and the prominence says how far that bump stands above the floor. Positive means late.

A measure is evidence, never a reason to write on its own, and it fails closed: no audio, no webrtcvad, or a prominence under the floor gives no opinion, and an enabled check with no opinion never verifies a file.
webrtcvad is imported only by read_onsets(), so the Bazarr hook stays NumPy only."""

import json
import math
import subprocess
import threading

import numpy

from .logs import LEVEL, log
from .reference import MEDIA_INPUT_OPTIONS
from .seam_audio import tagged_stream

FRAMES_PER_SECOND = 100
SAMPLE_RATE = 16000
FRAME_BYTES = 320
READ_FRAMES = 6000
AGGRESSIVENESS = 3
MIN_SILENCE = 0.25
MIN_SPEECH = 0.15
CLOSE_GAP = 0.10
MAX_LAG = 3.0
LAG_STEP = 0.01
KERNEL_SIGMA = 0.05
BASELINE = -0.09
MIN_ONSETS = 50
MIN_LINES = 8
FILE_PROMINENCE = 5.0
PASSAGE_PROMINENCE = 5.0
FINE_PROMINENCE = 3.5
MIN_PASSAGE_LINES = 25
RESIDUAL_LIMIT = 0.5
WINDOW_LINES = 40
WINDOW_STEP = 20
CENTRE_CHANNELS = 6
PROBE_TIMEOUT_SECONDS = 30
READ_TIMEOUT_SECONDS = 900
LAGS = numpy.arange(-MAX_LAG, MAX_LAG + LAG_STEP / 2, LAG_STEP)
EDGES = numpy.arange(-MAX_LAG - LAG_STEP / 2, MAX_LAG + LAG_STEP, LAG_STEP)
RADIUS = int(4 * KERNEL_SIGMA / LAG_STEP)
KERNEL = numpy.exp(-0.5 * (numpy.arange(-RADIUS, RADIUS + 1) * LAG_STEP / KERNEL_SIGMA) ** 2)
USABLE = numpy.abs(LAGS) <= MAX_LAG - 1.0


def onsets_of(mask):
    """The speech onsets of a mask (one truth value per 10 ms), in seconds, or None when there is too little to measure with."""
    if mask is None or not len(mask):
        return None
    edges = numpy.flatnonzero(numpy.diff(numpy.concatenate([[0], numpy.asarray(mask).astype(numpy.int8), [0]])))
    merged = []
    for first, last in zip(edges[::2], edges[1::2], strict=True):
        if merged and first - merged[-1][1] < CLOSE_GAP * FRAMES_PER_SECOND:
            merged[-1] = (merged[-1][0], last)
            continue
        merged.append((first, last))
    onsets, previous_end = [], -10 ** 9
    for first, last in merged:
        if last - first >= MIN_SPEECH * FRAMES_PER_SECOND and first - previous_end >= MIN_SILENCE * FRAMES_PER_SECOND:
            onsets.append(first / FRAMES_PER_SECOND)
        previous_end = last
    return numpy.array(onsets) if len(onsets) >= MIN_ONSETS else None


def measure(starts, onsets):
    """{"offset", "prominence", "lines"} of these line starts against the onsets, or None when no line has an onset within reach."""
    if onsets is None or len(starts) < MIN_LINES:
        return None
    moments = numpy.sort(numpy.asarray(starts, dtype=float))
    low, high = numpy.searchsorted(onsets, moments - MAX_LAG), numpy.searchsorted(onsets, moments + MAX_LAG)
    pairs = [moments[index] - onsets[low[index]:high[index]] for index in range(len(moments)) if high[index] > low[index]]
    if not pairs:
        return None
    histogram, _ = numpy.histogram(numpy.concatenate(pairs), bins=EDGES)
    density = numpy.convolve(histogram, KERNEL, mode="same")
    usable = density[USABLE]
    floor = numpy.median(usable)
    spread = 1.4826 * numpy.median(numpy.abs(usable - floor)) or 1.0
    best = int(usable.argmax())
    return {"offset": float(LAGS[USABLE][best] - BASELINE), "prominence": float((usable[best] - floor) / spread), "lines": len(moments)}


def settled(found, prominence=PASSAGE_PROMINENCE):
    return found is not None and found["prominence"] >= prominence and found["lines"] >= MIN_PASSAGE_LINES


def file_measure(starts, onsets):
    found = measure(starts, onsets)
    return found if settled(found, FILE_PROMINENCE) else None


def off_windows(starts, onsets, limit=RESIDUAL_LIMIT):
    """Rolling windows of lines (positions in `starts` as given, sorted by time) whose settled measure is beyond the limit: what would keep a file from being verified."""
    off = []
    for first in range(0, max(len(starts) - WINDOW_LINES, 0) + 1, WINDOW_STEP):
        positions = range(first, min(first + WINDOW_LINES, len(starts)))
        found = measure([starts[position] for position in positions], onsets)
        if settled(found) and abs(found["offset"]) > limit:
            off.append((positions, found))
    return off


def read_onsets(video, language=""):
    """The onsets of the video's speech: the audio stream in the reference's language (its centre channel when there is one), read with ffmpeg, judged by webrtcvad. None when anything is missing,
    when no stream can be told to be in that language, or when the read takes over READ_TIMEOUT_SECONDS."""
    try:
        import webrtcvad
    except ImportError:
        log("speech-check.unavailable", "webrtcvad is not installed, the speech check gives no opinion", LEVEL["warning"], video=video)
        return None
    stream = tagged_stream(video, language)
    if stream is None:
        log("speech-check.no-stream", f"no audio stream in the reference's language ({language or 'unknown'}), the speech check gives no opinion", LEVEL["warning"], video=video)
        return None
    described = stream_description(video, stream)
    if described is None:
        return None
    channels, start = described
    layout = ["-af", "pan=mono|c0=FC"] if channels >= CENTRE_CHANNELS else ["-ac", "1"]
    command = ["ffmpeg", "-nostdin", "-v", "error", *MEDIA_INPUT_OPTIONS, "-i", video, "-map", f"0:a:{stream}", *layout, "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    detector = webrtcvad.Vad(AGGRESSIVENESS)
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            # a stalled decoder blocks the read itself, so the time limit kills it from outside; the read then ends and the result is refused
            timer = threading.Timer(READ_TIMEOUT_SECONDS, process.kill)
            timer.start()
            try:
                parts = []
                while True:
                    block = process.stdout.read(FRAME_BYTES * READ_FRAMES)
                    if len(block) < FRAME_BYTES:
                        break
                    usable = len(block) // FRAME_BYTES * FRAME_BYTES
                    parts.append(numpy.array([detector.is_speech(block[at:at + FRAME_BYTES], SAMPLE_RATE) for at in range(0, usable, FRAME_BYTES)], dtype=bool))
                process.wait()
            finally:
                timer.cancel()
    except (OSError, subprocess.SubprocessError) as error:
        log("speech-check.unreadable", f"the audio could not be read: {error}", LEVEL["warning"], video=video)
        return None
    if process.returncode != 0 or not parts:
        log("speech-check.unreadable", "the audio could not be read", LEVEL["warning"], video=video)
        return None
    return onsets_of(numpy.concatenate([numpy.zeros(math.floor(max(start, 0.0) * FRAMES_PER_SECOND), dtype=bool), *parts]))


def stream_description(video, stream):
    # how many channels the stream has, and how long after the file's own start it begins (the mask counts from the video's start)
    command = ["ffprobe", "-v", "error", "-select_streams", f"a:{stream}", "-show_entries", "stream=channels,start_time:format=start_time", "-of", "json", video]
    try:
        described = json.loads(subprocess.run(command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, check=True).stdout)
        return int(described["streams"][0]["channels"]), float(described["streams"][0].get("start_time", 0.0)) - float(described.get("format", {}).get("start_time", 0.0))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as error:
        log("speech-check.unreadable", f"the audio stream could not be described: {error}", LEVEL["warning"], video=video)
        return None
