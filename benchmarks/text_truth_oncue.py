# SPDX-License-Identifier: GPL-3.0-or-later
"""The engine text_truth.py measures by default: OnCue's own judge, confidence and automatic band, without a video."""
from oncue import alignment
from oncue.confidence import confidence
from oncue.guard import summarize
from oncue.reference import choose_reference


def run(cues, tracks, hearing_impaired, texts):
    reference = choose_reference(tracks, hearing_impaired, len(cues))
    if reference is None or len(cues) < alignment.MIN_CUES:
        return {"decision": "unverified", "confidence": 0, "timings": None}
    judged = alignment.judge(cues, reference)
    decision, _, _, attempt = judged
    score, _ = confidence(summarize(reference["kind"], *judged))
    timings = attempt["timings"] if decision == "fixed" else None
    return {"decision": decision, "confidence": score, "timings": timings}
