# SPDX-License-Identifier: GPL-3.0-or-later
"""Speech activity around passage seams only: a few seconds of audio around each line the text cannot place, at its current place and at
each place a neighbouring passage would take it, read with ffmpeg and judged by webrtcvad (an energy threshold when it is not installed).
The result is a speech mask (one value per 10 ms, NaN where nothing was read) that text_align uses as weak evidence for a side."""

import json
import math
import subprocess

import numpy

from .logs import LEVEL, log
from .reference import MEDIA_INPUT_OPTIONS

SAMPLE_RATE = 16000
FRAME_SECONDS = 0.01
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_SECONDS)
VAD_FRAME_SAMPLES = 480
AGGRESSIVENESS = 3
MARGIN_SECONDS = 1.0
MAX_WINDOWS = 40
WINDOW_TIMEOUT_SECONDS = 30
ENERGY_RATIO = 4.0


LANGUAGE_CODES = {"eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "spa": "es", "ita": "it", "por": "pt", "dut": "nl", "nld": "nl",
                  "jpn": "ja", "kor": "ko", "chi": "zh", "zho": "zh", "rus": "ru", "pol": "pl", "swe": "sv", "dan": "da", "nor": "no", "fin": "fi"}
UNDETERMINED = "und"
STREAMS = {}


def provider(video, language=""):
    """A function giving the speech mask for a list of (start, end) windows of this video, or None when the audio cannot be read. The
    audio stream in the reference's language is read (a dub on the first stream would put speech where the original has none)."""
    def speech(windows):
        return speech_mask(video, windows, audio_stream(video, language))
    return speech


def audio_stream(video, language):
    """The position among audio streams of the one tagged in this language (the original, whose words the reference transcribes), else
    the first."""
    found = tagged_stream(video, language)
    return 0 if found is None else found


def tagged_stream(video, language):
    """The position among audio streams of the one tagged in this language, or None when it cannot be told: several audio streams and none
    in this language, or a single one tagged with another language (a dub, whose speech the reference does not follow)."""
    wanted = LANGUAGE_CODES.get(language.lower(), language.lower())
    if (video, wanted) not in STREAMS:
        STREAMS[video, wanted] = stream_languages(video)
    codes = STREAMS[video, wanted]
    if wanted and wanted in codes:
        return codes.index(wanted)
    if len(codes) == 1 and (not wanted or codes[0] in ("", UNDETERMINED)):
        return 0
    return None


def stream_languages(video):
    # one entry per audio stream, "" for a stream without a language tag
    command = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index:stream_tags=language", "-of", "json", video]
    try:
        streams = json.loads(subprocess.run(command, capture_output=True, text=True, timeout=WINDOW_TIMEOUT_SECONDS, check=False).stdout or "{}").get("streams", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    tags = [(stream.get("tags") or {}).get("language", "").strip().lower() for stream in streams]
    return [LANGUAGE_CODES.get(tag, tag) for tag in tags]


def speech_mask(video, windows, stream=0):
    merged = merge([(max(0.0, start - MARGIN_SECONDS), end + MARGIN_SECONDS) for start, end in windows])[:MAX_WINDOWS]
    if not merged:
        return None
    mask = numpy.full(math.ceil(merged[-1][1] / FRAME_SECONDS) + 1, numpy.nan, dtype=numpy.float32)
    for start, end in merged:
        samples = read_audio(video, start, end, stream)
        if samples is None:
            return None
        frames = voiced_frames(samples)
        first = int(start / FRAME_SECONDS)
        mask[first:first + len(frames)] = frames[:len(mask) - first]
    return mask


def merge(windows):
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    return merged


def read_audio(video, start, end, stream=0):
    # -ss before -i seeks the audio stream accurately and fast; subtitles are not read here
    command = ["ffmpeg", "-nostdin", "-v", "error", *MEDIA_INPUT_OPTIONS, "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", video,
               "-map", f"0:a:{stream}", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    try:
        finished = subprocess.run(command, capture_output=True, timeout=WINDOW_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        log("seam-audio.unreadable", f"audio around a seam could not be read: {error}", LEVEL["warning"], video=video)
        return None
    if finished.returncode != 0:
        log("seam-audio.unreadable", "audio around a seam could not be read", LEVEL["warning"], video=video, stderr=finished.stderr[-300:].decode(errors="replace"))
        return None
    return numpy.frombuffer(finished.stdout, dtype=numpy.int16)


def voiced_frames(samples):
    """One value per 10 ms: 1 for speech, 0 for none."""
    try:
        import webrtcvad
    except ImportError:
        return energy_frames(samples)
    detector = webrtcvad.Vad(AGGRESSIVENESS)
    count = len(samples) // VAD_FRAME_SAMPLES
    voiced = [detector.is_speech(samples[index * VAD_FRAME_SAMPLES:(index + 1) * VAD_FRAME_SAMPLES].tobytes(), SAMPLE_RATE) for index in range(count)]
    return numpy.repeat(numpy.array(voiced, dtype=numpy.float32), VAD_FRAME_SAMPLES // FRAME_SAMPLES)


def energy_frames(samples):
    count = len(samples) // FRAME_SAMPLES
    if count == 0:
        return numpy.zeros(0, dtype=numpy.float32)
    energy = numpy.sqrt(numpy.mean(samples[:count * FRAME_SAMPLES].astype(numpy.float32).reshape(count, FRAME_SAMPLES) ** 2, axis=1))
    floor = max(float(numpy.percentile(energy, 20)), 1.0)
    return (energy > ENERGY_RATIO * floor).astype(numpy.float32)
