# SPDX-License-Identifier: GPL-3.0-or-later
"""An engine for text_truth.py and heldout.py: OnCue's timing judge, then the grouped text alignment with calibrated passage writes,
through the guard's own IO-free decision (guard.decide), its confidence and its output check, without a video.

ONCUE_TEXT_MODEL: folder of the sentence model; only needed for texts missing from ONCUE_TEXT_VECTORS.
ONCUE_TEXT_VECTORS: an .npz of line vectors (texts, vectors); used first.
ONCUE_TEXT_SPEECH: "1" to use the speech masks the harness passes as seam evidence (off by default, as ONCUE_TEXT_SEAM_AUDIO in OnCue).
ONCUE_TEXT_TIMING: "0" to skip the timing judge (text-only outcome fields; decisions are the text's either way).
ONCUE_TEXT_FAMILY: which calibration the vectors need ("e5" or "labse").
ONCUE_TEXT_SPEECH_CHECK: "0" to leave the speech check off; otherwise the speech masks the harness passes feed it (no mask: it has no opinion, as in OnCue with no audio).
ONCUE_WRITE_SAFETY: overrides the calibrated safety a passage needs to be written (text_confidence.WRITE_SAFETY).
ONCUE_REFINE_PASSES: how many passes the text check may run (1 = no refine pass; default text_check.MAX_PASSES).
ONCUE_WHISPER_FOLDER: recognised speech at seams (<fingerprint>.json per video: words with times, and the windows that were read), used by
  the refine passes as evidence; a window that was not read gives no opinion. ONCUE_WHISPER_RECORD: a .jsonl file where the windows the
  refine passes ask for are written (no opinion is given), to know which clips to transcribe.
"""
import hashlib
import json
import os

import numpy

from oncue import alignment, guard, speech_check, text_check, text_confidence
from oncue.confidence import confidence
from oncue.guard import timing_flaw, with_text
from oncue.outcome import DECISION
from oncue.reference import choose_reference
from oncue.subtitles import parse_subtitle
from oncue.text_model import load_model

MODEL_FOLDER = os.environ.get("ONCUE_TEXT_MODEL", "")
VECTORS_FILE = os.environ.get("ONCUE_TEXT_VECTORS", "")
USE_SPEECH = os.environ.get("ONCUE_TEXT_SPEECH", "0") == "1"
SPEECH_CHECK = os.environ.get("ONCUE_TEXT_SPEECH_CHECK", "1") != "0"
USE_TIMING = os.environ.get("ONCUE_TEXT_TIMING", "1") != "0"
THREADS = int(os.environ.get("ONCUE_TEXT_THREADS", "1"))
FAMILY = os.environ.get("ONCUE_TEXT_FAMILY", "e5")
PASSES = int(os.environ.get("ONCUE_REFINE_PASSES", str(text_check.MAX_PASSES)))
WHISPER_FOLDER = os.environ.get("ONCUE_WHISPER_FOLDER", "")
WHISPER_RECORD = os.environ.get("ONCUE_WHISPER_RECORD", "")
CACHE = {}
AUTOMATIC = 90
for name, value in os.environ.items():
    # engine constants for experiments on development data: ONCUE_X_<MODULE>__<CONSTANT>=value (modules text_check, text_confidence, speech_check)
    if name.startswith("ONCUE_X_"):
        module_name, constant = name[len("ONCUE_X_"):].lower().split("__", 1)
        module = {"text_check": text_check, "text_confidence": text_confidence, "speech_check": speech_check}[module_name]
        setattr(module, constant.upper(), type(getattr(module, constant.upper()))(float(value)) if not isinstance(getattr(module, constant.upper()), bool) else value == "1")
if os.environ.get("ONCUE_WRITE_SAFETY"):
    text_confidence.WRITE_SAFETY = float(os.environ["ONCUE_WRITE_SAFETY"])


def production_flaw(cues, timings):
    lines = []
    for number, (start, end, line_text) in enumerate(cues, 1):
        lines.append(f"{number}\n{stamp(start)} --> {stamp(end)}\n{line_text or '-'}\n")
    document = parse_subtitle("", raw="\n".join(lines).encode())
    return timing_flaw(document, timings)


def stamp(seconds):
    milliseconds = round(max(seconds, 0.0) * 1000)
    return f"{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d},{milliseconds % 1000:03d}"


def run(cues, tracks, hearing_impaired, texts, speech=None):
    reference = choose_reference(tracks, hearing_impaired, len(cues))
    if reference is None or len(cues) < alignment.MIN_CUES:
        return {"decision": "unverified", "confidence": 0, "timings": None, "weak_minutes": []}
    text = (text_check.decide(cues, texts, embed, speech if USE_SPEECH else None, family=FAMILY, passes=PASSES, listen=listener(texts),
                              output_check=lambda timings: production_flaw(cues, timings), speech_check_on=SPEECH_CHECK, onsets=speech_check.onsets_of(speech) if SPEECH_CHECK else None) if texts
            else text_check.unavailable("no reference text", cues))
    if USE_TIMING:
        outcome, fix = guard.decide(cues, reference, text)
    else:
        original = alignment.score(cues, reference)
        outcome = {"reference": reference["kind"], "before": original, "after": original, "decision": DECISION["kept"], "weak_minutes": []}
        outcome, fix = with_text(outcome, text, cues, reference, {})
    timings = fix["timings"] if fix else None
    score, band = confidence(outcome)
    if timings and score >= AUTOMATIC and production_flaw(cues, timings):
        # the guard's own output check (rendered file, order, new overlaps) would refuse the write: it stays a proposal
        score, band = AUTOMATIC - 1, "medium"
    return {"decision": outcome["decision"], "confidence": score, "band": band, "timings": timings, "weak_minutes": outcome.get("weak_minutes") or [],
            "gaps": outcome.get("gaps") or [],
            "detail": {"text": {key: value for key, value in text.items() if key not in ("timings", "proposal", "candidates", "file_features", "vetting")},
                       "proposal_evidence": outcome.get("proposal_evidence"), "reason": outcome.get("reason", ""),
                       "candidates": [{"lines": len(candidate["lines"]), "kind": candidate["kind"], "safety": round(candidate["safety"], 3),
                                       "written": candidate["written"], "pass": candidate.get("pass", 1), "origin": candidate.get("origin", "first"),
                                       "first": candidate["lines"][0], "last": candidate["lines"][-1], "offset": round(candidate["offset"], 3),
                                       "refused_by_speech": bool(candidate.get("refused_by_speech"))}
                                      for candidate in text.get("candidates", [])]}}


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


def fingerprint(texts):
    # which video a case belongs to, from its reference track (the harness passes no video): its first lines and their times
    return hashlib.sha1("\n".join(f"{start:.2f} {text}" for start, _, text in (texts or [])[:40]).encode()).hexdigest()[:16]


def listener(texts):
    if WHISPER_RECORD:
        key = fingerprint(texts)

        def record(requests):
            with open(WHISPER_RECORD, "a") as handle:
                handle.write(json.dumps({"fingerprint": key, "windows": [(round(start, 2), round(end, 2)) for start, end, _ in requests]}) + "\n")
            return [None] * len(requests)
        return record
    if not WHISPER_FOLDER:
        return None
    path = os.path.join(WHISPER_FOLDER, fingerprint(texts) + ".json")
    if not os.path.exists(path):
        return None
    if path not in CACHE:
        with open(path) as handle:
            CACHE[path] = json.load(handle)
    heard = CACHE[path]
    return lambda requests: text_check.listen_agreements(requests, heard["words"], heard["windows"], embed)
