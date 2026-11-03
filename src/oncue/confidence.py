# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn a decision and its measurements into a 0-100 confidence score and a band."""

import math

from . import text_confidence
from .alignment import OBJECTIVE_MARGIN, PASS_MARGIN
from .outcome import DECISION
from .text_check import VERDICT

FULL_CONFIDENCE_MARGIN = 0.4
WINDOW_FLOOR = 0.2
FULL_CONFIDENCE_WINDOW = 0.5
WEAK_RUN_FLOOR = -8.0
WEAK_RUN_CLEAR = -3.0
FULL_CONFIDENCE_OBJECTIVE_MARGIN = 0.3
STRENGTH_FLOOR = 0.3
AUDIO_SYNC_CONFIDENCE = 30
AUDIO_MIN_PARTS = 4
TEXT_SETTLED_CONFIDENCE = 90
FILE_SAFETY_FLOOR = 0.9
TEXT_UNSETTLED_CEILING = 89
TIMING_ONLY_CEILING = 89
BANDS = ((90, "high"), (60, "medium"), (1, "low"), (0, "none"))
AUDIO_REFERENCE = "audio"


def confidence(outcome):
    score = with_text(outcome, confidence_score(outcome))
    return score, next(band for floor, band in BANDS if score >= floor)


def with_text(outcome, score):
    """When the text check ran it alone decides the automatic band: a written correction scores from the chance that all its passages are
    right (each passed its gate), a confirmed file from its calibrated verification, both mapped onto 90-100; anything else is capped. A
    correction whose chance falls under FILE_SAFETY_FLOOR stays under the automatic band: it is a proposal, never written on its own.
    Without it, a timing fix never reaches the automatic band (audio-only syncs keep their own setting)."""
    text = outcome.get("text")
    if not text and outcome["decision"] == DECISION["fixed"] and outcome.get("reference") != AUDIO_REFERENCE:
        # a timing fix the text did not read is only a proposal: timing alone slides passages onto neighbouring lines
        return min(score, TIMING_ONLY_CEILING)
    if not text or outcome["decision"] not in (DECISION["kept"], DECISION["fixed"]):
        return score
    if text["verdict"] == VERDICT["corrected"]:
        file_safety = text.get("file_safety", text.get("safety", 0.0))
        if file_safety < FILE_SAFETY_FLOOR:
            return min(round(100 * file_safety), TEXT_UNSETTLED_CEILING)
        return above(file_safety, FILE_SAFETY_FLOOR)
    if text["verdict"] == VERDICT["confirmed"]:
        return above(text.get("verified", 0.0), text_confidence.VERIFY_SAFETY)
    return min(score, TEXT_UNSETTLED_CEILING)


def above(probability, threshold):
    if threshold >= 1:
        return 100
    return min(100, TEXT_SETTLED_CONFIDENCE + round(10 * max(0.0, probability - threshold) / (1 - threshold)))


def confidence_score(outcome):
    if outcome["decision"] == DECISION["audio_synced"]:
        agreement = outcome.get("agreement") or {}
        if agreement.get("parts", 0) < AUDIO_MIN_PARTS:
            return AUDIO_SYNC_CONFIDENCE
        return round(100 * agreement["agreeing"] / agreement["parts"])
    if outcome["decision"] not in (DECISION["kept"], DECISION["fixed"]):
        return 0
    after = outcome["after"]
    components = [share(after["matched"] - after["noise"] - PASS_MARGIN, FULL_CONFIDENCE_MARGIN - PASS_MARGIN),
                  share(after["worst_margin"] - WINDOW_FLOOR, FULL_CONFIDENCE_WINDOW - WINDOW_FLOOR),
                  share(after["weak_run"] - WEAK_RUN_FLOOR, WEAK_RUN_CLEAR - WEAK_RUN_FLOOR)]
    if outcome["decision"] == DECISION["fixed"] and "objective" in outcome:
        components += [share(outcome["objective"] - outcome["null_objective"] - OBJECTIVE_MARGIN, FULL_CONFIDENCE_OBJECTIVE_MARGIN - OBJECTIVE_MARGIN),
                       share(outcome.get("strength", 1.0) - STRENGTH_FLOOR, 1 - STRENGTH_FLOOR)]
    return round(min(100, max(1, 50 + 50 * min(components))))


def share(value, full):
    if math.isnan(value):
        return 0.0
    return min(1.0, max(0.0, value / full))
