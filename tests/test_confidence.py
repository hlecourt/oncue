# SPDX-License-Identifier: GPL-3.0-or-later
import numpy
from synthetic import dialogue, translation

from oncue.alignment import judge
from oncue.confidence import confidence, confidence_score
from oncue.guard import summarize

STRONG = {"matched": 0.9, "noise": 0.3, "worst_margin": 0.6, "weak_run": -0.5, "passed": True}
BORDERLINE = {"matched": 0.6, "noise": 0.3, "worst_margin": 0.15, "weak_run": -1.0, "passed": True}


def test_kept_files_score_by_their_margins_and_their_weakest_run():
    assert confidence({"decision": "kept", "after": STRONG}) == (100, "high")
    assert confidence({"decision": "kept", "after": BORDERLINE})[1] == "low"
    assert confidence({"decision": "kept", "after": {**STRONG, "weak_run": -5.5}}) == (75, "medium")


def test_fixed_files_score_by_null_margin_and_segment_strength():
    fixed = {"decision": "fixed", "after": STRONG, "objective": 1.1, "null_objective": 0.7, "strength": 1.0}
    assert confidence_score(fixed) == 100
    assert confidence({**fixed, "objective": 0.85})[1] == "low"
    assert confidence({**fixed, "strength": 0.3})[1] == "low"


def test_an_unexplained_passage_inside_a_kept_file_is_not_trusted():
    starts, ends = dialogue(6)
    cues = [(start, end, "line") for start, end in zip(starts, ends, strict=False)]
    rng = numpy.random.default_rng(6)
    middle = len(cues) // 2
    first, last = cues[middle][0], cues[middle + 15][0]
    for index in range(middle, middle + 15):
        moment = rng.uniform(first, last)
        cues[index] = (moment, moment + 1.5, "line")
    outcome = summarize("eng-text", *judge(sorted(cues), {"kind": "eng-text", "starts": starts, "ends": ends}))
    assert outcome["decision"] == "kept" and confidence(outcome)[0] < 90
    assert set(outcome["weak_minutes"]) & set(range(int(first // 60), int(last // 60) + 1))


def test_unsettled_decisions():
    assert confidence({"decision": "rejected"}) == (0, "none")
    assert confidence({"decision": "wrong-episode"}) == (0, "none")
    assert confidence({"decision": "audio-synced", "reason": ""}) == (30, "low")


def test_clean_realignments_are_high_confidence():
    starts, ends = dialogue(3)
    cues = translation(starts, ends, 3)
    reference = {"kind": "eng-text", "starts": starts, "ends": ends}
    for transform in (lambda value: value + 21.0, lambda value: value * 23.976 / 25 + 4, lambda value: value if value < 500 else value - 18):
        shifted = [(transform(start), transform(end), text) for start, end, text in cues]
        outcome = summarize("eng-text", *judge(shifted, reference))
        assert outcome["decision"] == "fixed"
        assert confidence_score(outcome) >= 90, confidence_score(outcome)


def test_random_times_have_no_confidence():
    starts, ends = dialogue(4)
    rng = numpy.random.default_rng(4)
    cues = sorted((float(start), float(start) + 2, "x") for start in rng.uniform(0, starts[-1], 300))
    outcome = summarize("eng-text", *judge(cues, {"kind": "eng-text", "starts": starts, "ends": ends}))
    assert confidence(outcome) == (0, "none")


def test_a_timing_fix_the_text_did_not_read_stays_a_proposal():
    fixed = {"decision": "fixed", "reference": "eng-text", "after": STRONG, "objective": 1.1, "null_objective": 0.7, "strength": 1.0}
    assert confidence(fixed) == (89, "medium")
    assert confidence({**fixed, "decision": "kept"})[0] == 100


def test_a_correction_under_the_file_safety_floor_stays_under_the_automatic_band():
    fixed = {"decision": "fixed", "reference": "eng-text", "after": STRONG, "objective": 1.1, "null_objective": 0.7, "strength": 1.0}
    for file_safety, band in ((0.95, "high"), (0.9, "high"), (0.895, "medium"), (0.37, "low")):
        assert confidence({**fixed, "text": {"verdict": "corrected", "file_safety": file_safety}})[1] == band, file_safety
