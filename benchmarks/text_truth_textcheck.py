# SPDX-License-Identifier: GPL-3.0-or-later
"""An engine for text_truth.py: OnCue's timing judge followed by its text check, through the same code as the guard (text_check.decide,
guard.with_text, confidence), without a video.

ONCUE_TEXT_MODEL: folder of the sentence model (onnx/model.onnx, tokenizer.json, ...), required.
ONCUE_TEXT_VECTORS: an .npz from text_truth_embeddings.py; its vectors are used first, the model only for texts it lacks.
ONCUE_TEXT_AUDIO: a speech second-opinion module (path to a .py with off_windows(timings, speech)); when set, a window off by audio caps
the confidence under 90 unless the text settled the result.
"""
import importlib.util
import os

import numpy

from oncue import alignment, text_check
from oncue.confidence import confidence
from oncue.guard import summarize, with_text
from oncue.outcome import DECISION
from oncue.reference import choose_reference
from oncue.text_model import load_model

MODEL_FOLDER = os.environ.get("ONCUE_TEXT_MODEL", "")
LOOSE_SHARE = os.environ.get("ONCUE_TEXT_LOOSE")
AUDIO_MODULE = os.environ.get("ONCUE_TEXT_AUDIO", "")
THREADS = int(os.environ.get("ONCUE_TEXT_THREADS", "0"))
VECTORS_FILE = os.environ.get("ONCUE_TEXT_VECTORS", "")
AUDIO_CEILING = 89
CACHE = {}


def run(cues, tracks, hearing_impaired, texts, speech=None):
    reference = choose_reference(tracks, hearing_impaired, len(cues))
    if reference is None or len(cues) < alignment.MIN_CUES:
        return {"decision": "unverified", "confidence": 0, "timings": None}
    judged = alignment.judge(cues, reference)
    outcome = summarize(reference["kind"], *judged)
    attempt = outcome.pop("attempt", None)
    timings = attempt["timings"] if outcome["decision"] == DECISION["fixed"] else None
    if LOOSE_SHARE:
        text_check.MAX_LOOSE_SHARE = float(LOOSE_SHARE)
    text = None
    if MODEL_FOLDER:
        text = text_check.decide(cues, texts, embed) if texts else text_check.unavailable("no reference text", cues)
    if text:
        outcome, text_fix = with_text(outcome, text, cues, reference, {})
        if text_fix:
            timings = text_fix["timings"]
        elif outcome["decision"] == DECISION["kept"]:
            timings = None
    score, _ = confidence(outcome)
    settled = bool(text) and text["verdict"] != text_check.VERDICT["unsettled"]
    flags = audio_flags(timings or cues, speech) if AUDIO_MODULE and speech is not None and outcome["decision"] in ("kept", "fixed") else []
    if flags and not settled:
        score = min(score, AUDIO_CEILING)
    return {"decision": outcome["decision"], "confidence": score, "timings": timings,
            "detail": {"text": {key: value for key, value in (text or {}).items() if key != "timings"}, "audio_flags": len(flags)}}


def embed(lines):
    if "vectors" not in CACHE:
        CACHE["vectors"] = {}
        if VECTORS_FILE:
            stored = numpy.load(VECTORS_FILE, allow_pickle=True)
            CACHE["vectors"] = dict(zip(stored["texts"].tolist(), stored["vectors"], strict=True))
    missing = [line for line in dict.fromkeys(lines) if line not in CACHE["vectors"]]
    if missing:
        if "model" not in CACHE:
            CACHE["model"] = load_model(MODEL_FOLDER, THREADS)
        for line, vector in zip(missing, CACHE["model"].encode(missing), strict=True):
            CACHE["vectors"][line] = vector
    return numpy.array([CACHE["vectors"][line] for line in lines])


def audio_flags(timings, speech):
    if "audio" not in CACHE:
        specification = importlib.util.spec_from_file_location("audio_second_opinion", AUDIO_MODULE)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        CACHE["audio"] = module
    return CACHE["audio"].off_windows([(start, end, text) for start, end, text in timings], speech)
