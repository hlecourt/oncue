# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn a decision and its measurements into a 0-100 confidence score and a band."""

from .alignment import (
    MAX_UNSUPPORTED_SHARE,
    OBJECTIVE_MARGIN,
    PASS_MARGIN,
    WINDOW_PASS_MARGIN,
)
from .outcome import DECISION

FULL_CONFIDENCE_MARGIN = 0.25
FULL_CONFIDENCE_OBJECTIVE_MARGIN = 0.3
WEAK_MINUTE_PENALTY = 10
AUDIO_SYNC_CONFIDENCE = 30
BANDS = ((80, "high"), (60, "medium"), (1, "low"), (0, "none"))


def confidence(outcome):
    score = confidence_score(outcome)
    return score, next(band for floor, band in BANDS if score >= floor)


def confidence_score(outcome):
    if outcome["decision"] == DECISION["audio_synced"]:
        return AUDIO_SYNC_CONFIDENCE
    if outcome["decision"] not in (DECISION["kept"], DECISION["fixed"]):
        return 0
    after = outcome["after"]
    window = share(after["worst_window"] - after["noise"] - WINDOW_PASS_MARGIN, FULL_CONFIDENCE_MARGIN)
    if outcome["decision"] == DECISION["kept"]:
        components = (share(after["matched"] - after["noise"] - PASS_MARGIN, FULL_CONFIDENCE_MARGIN), window)
    else:
        components = (share(outcome["objective"] - outcome["null_objective"] - OBJECTIVE_MARGIN, FULL_CONFIDENCE_OBJECTIVE_MARGIN), window,
                      1 - share(outcome.get("unsupported_share", 0), MAX_UNSUPPORTED_SHARE / 2))
    score = 50 + 50 * min(components) - WEAK_MINUTE_PENALTY * len(outcome.get("weak_minutes", []))
    return round(min(100, max(1, score)))


def share(value, full):
    return min(1.0, max(0.0, value / full))
