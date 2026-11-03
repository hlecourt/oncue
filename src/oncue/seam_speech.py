# SPDX-License-Identifier: GPL-3.0-or-later
"""Recognised speech around passage seams, for the text check's refine passes: a few seconds of audio around each line still in doubt, at
its current place and at the place a written passage would take it, read with ffmpeg and transcribed in the spoken language by
faster-whisper (base, int8, word times), then compared with the line (text_check.listen_agreements). Evidence only: it settles which side
a seam line belongs to and feeds the calibrated safety, never a write on its own. Optional: without faster-whisper or a readable audio
track it gives no opinion."""

import os
import time

import numpy

from . import seam_audio
from .logs import LEVEL, log

MODEL_SIZE = "base"
CPU_THREADS = 2
MAX_WINDOW_SECONDS = 30.0
MERGE_GAP = 1.0
STATE = {}


def listener(video, embed, language="", model_folder=""):
    """A function [(start, end, line[, reference line])] -> [agreement or None] for this video (no opinion at all when faster-whisper is not
    installed). The audio stream in the reference's language is read and Whisper is told that language (short clips mislead its detection);
    the model loads on the first request, so files with nothing in doubt pay nothing."""
    stream = None

    def listen(requests):
        nonlocal stream
        model = whisper_model(model_folder)
        if model is None:
            return [None] * len(requests)
        if stream is None:
            stream = seam_audio.audio_stream(video, language)
        spoken = seam_audio.LANGUAGE_CODES.get(language.lower(), language.lower()) or None
        started = time.time()
        windows = merged([(request[0], request[1]) for request in requests])
        words = []
        for start, end in windows:
            samples = seam_audio.read_audio(video, start, end, stream)
            if samples is None:
                return [None] * len(requests)
            words.extend(transcribe(model, samples, start, spoken if spoken and len(spoken) == 2 else None))
        words.sort()
        log("seam-speech.read", f"{len(windows)} windows, {sum(end - start for start, end in windows):.0f} s of audio in {time.time() - started:.1f} s",
            video=video, windows=len(windows), words=len(words))
        from .text_check import listen_agreements
        return listen_agreements(requests, words, windows, embed)
    return listen


def whisper_model(model_folder):
    if "model" in STATE:
        return STATE["model"]
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        STATE["model"] = None
        return None
    source = model_folder if model_folder and os.path.isdir(model_folder) else MODEL_SIZE
    try:
        STATE["model"] = WhisperModel(source, device="cpu", compute_type="int8", cpu_threads=CPU_THREADS)
    except (OSError, RuntimeError, ValueError) as error:
        log("seam-speech.unavailable", f"speech recognition model could not load: {error}", LEVEL["warning"])
        STATE["model"] = None
    return STATE["model"]


def merged(spans):
    windows = []
    for start, end in sorted(spans):
        if windows and start <= windows[-1][1] + MERGE_GAP and end - windows[-1][0] <= MAX_WINDOW_SECONDS:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
            continue
        windows.append((max(0.0, start), end))
    return windows


def transcribe(model, samples, offset, language=None):
    # never given the expected text; speech-only filtering keeps Whisper from inventing words on music
    audio = samples.astype(numpy.float32) / 32768
    segments, _ = model.transcribe(audio, language=language, word_timestamps=True, vad_filter=True, condition_on_previous_text=False, beam_size=1)
    return [(word.start + offset, word.end + offset, word.word) for segment in segments for word in segment.words or []]
