# SPDX-License-Identifier: GPL-3.0-or-later
"""Confidence for the text check, in two separate outcomes.

- "This change is safe": a candidate passage may be written. A logistic model over the passage's evidence, each weight constrained to the
  direction its feature can only push (more contradictions never make a move safer), mapped through an isotonic table calibrated apart for
  first-pass candidates and refine alternatives; fitted on one third of the episodes and calibrated on another (benchmarks); coefficients
  stored in text_models, numpy only. A candidate with any feature outside what the model was fitted and calibrated on is refused: the model
  never extrapolates.
- "The whole file is verified": only then green. A rule, not a model: no line in doubt at all (no hard doubt, no soft doubt). On the
  development episodes every file meeting it was right; a fitted model let wrong files through on the untouched third."""

import itertools
import math

from . import text_models

WRITE_SAFETY = 0.985
GATE_STEP = 0.001
INTERLEAVED_SHARE = 0.02
WHOLE_MOVE_SHARE = 0.5
MINORITY_ANCHORED = 0.4
STAYS_BETTER_GAIN = -1.5
# log(1 + passages): a file in a dozen pieces has no structure the time prior or a passage move can be trusted on
MESSY_FILE_PASSAGES = 2.56
ENVELOPE_SLACK = 1e-5
REFINE_ORIGINS = ("origin_core", "origin_merged", "origin_extended", "origin_seam", "origin_edge")
VERIFY_SAFETY = 1.0


def passage_safety(features, family="e5"):
    models = text_models.PASSAGE_MODELS.get(family)
    if models is None or interleaved_streams(features) or features["file_passages"] >= MESSY_FILE_PASSAGES or stays_better(features):
        return 0.0
    return calibrated(models["refine" if any(features.get(name, 0.0) for name in REFINE_ORIGINS) else "first"], features)


def fine_safety(features, family="e5"):
    """A small passage move the text and the speech both measure, gated on the speech's prominence under the lines it writes: at the gate a
    Learn-then-Test passed on the calibration titles (benchmarks) its safety is what that test bounds, one less the risk it was run at; with no
    gate passed for the family a small move is only offered, never written."""
    gate = text_models.FINE_GATES.get(family)
    if gate is None:
        return 0.0
    if gate["prominence"] is None or features["fine_prominence"] < gate["prominence"]:
        return WRITE_SAFETY - GATE_STEP
    return round(1.0 - gate["risk"], 6)


def file_safety(safeties):
    """The chance that every change written in the file is right, not just its weakest passage's: ten passages at 0.99 are not one."""
    return math.prod(safeties) if safeties else 0.0


def interleaved_streams(features):
    # unread lines sitting on other lines all through a move, one of them reading better where it is: two streams crossed, and nothing says
    # which unread line follows which (the right result often cannot even be written in file order); a move of most of the file has no other stream
    return features["file_share"] < WHOLE_MOVE_SHARE and features["interleaved"] / features["lines"] >= INTERLEAVED_SHARE and features["worst_gain"] < 0


def stays_better(features):
    # a short move mostly of lines it could not read, one of which reads clearly better where it is
    return features["file_share"] < WHOLE_MOVE_SHARE and features["anchored_share"] < MINORITY_ANCHORED and features["worst_gain"] <= STAYS_BETTER_GAIN


def file_verified(features):
    return 1.0 if features["hard"] == 0 and features["soft"] == 0 else 0.0


def calibrated(model, features):
    """The probability the candidate is right, and on which side of the write threshold it stands: the model's gate, chosen on the raw
    score on calibration episodes (benchmarks), decides; the table alone only says how sure."""
    if model is None or outside(model, features):
        return 0.0
    value = model["intercept"] + sum(weight * (features[name] - model["means"][name]) / model["scales"][name] for name, weight in model["weights"].items())
    raw = 1 / (1 + math.exp(-max(min(value, 40.0), -40.0)))
    probability = isotonic(model["table"], raw)
    return max(probability, WRITE_SAFETY) if raw >= model["gate"] else min(probability, WRITE_SAFETY - GATE_STEP)


def outside(model, features):
    # beyond the envelope of the fitting and calibration rows, a probability would be an extrapolation; a feature the model expects and
    # the check no longer measures means the model belongs to another version
    if any(name not in features for name in model["weights"]):
        return True
    for name, (low, high) in (model.get("bounds") or {}).items():
        value = features.get(name, 0.0)
        if value < low - ENVELOPE_SLACK or (high is not None and value > high + ENVELOPE_SLACK):
            return True
    return False


def isotonic(table, raw):
    # the calibration table: (raw score, calibrated probability) points, increasing; linear in between, flat outside
    if not table:
        return raw
    if raw <= table[0][0]:
        return table[0][1]
    for (low, low_value), (high, high_value) in itertools.pairwise(table):
        if raw <= high:
            return low_value + (high_value - low_value) * (raw - low) / max(high - low, 1e-12)
    return table[-1][1]
