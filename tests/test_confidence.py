import numpy

from subtitle_guard.alignment import judge
from subtitle_guard.confidence import confidence
from subtitle_guard.guard import summarize
from synthetic import dialogue, translation

STRONG = {"matched": 0.85, "noise": 0.2, "worst_window": 0.7, "passed": True}
BORDERLINE = {"matched": 0.48, "noise": 0.2, "worst_window": 0.33, "passed": True}


def test_kept_files_score_by_their_margins_and_weak_minutes():
    assert confidence({"decision": "kept", "after": STRONG}) == (100, "high")
    assert confidence({"decision": "kept", "after": BORDERLINE})[1] == "low"
    assert confidence({"decision": "kept", "after": STRONG, "weak_minutes": [3, 4]}) == (80, "high")
    assert confidence({"decision": "kept", "after": STRONG, "weak_minutes": [3, 4, 5]})[1] == "medium"


def test_fixed_files_score_by_null_margin_and_support():
    fixed = {"decision": "fixed", "after": STRONG, "objective": 1.1, "null_objective": 0.7, "unsupported_share": 0.0}
    assert confidence(fixed)[1] == "high" and confidence(fixed)[0] >= 90
    assert confidence({**fixed, "objective": 0.9})[1] == "low"
    assert confidence({**fixed, "unsupported_share": 0.04})[1] == "low"


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
        assert confidence(outcome)[1] == "high", confidence(outcome)


def test_random_times_have_no_confidence():
    starts, ends = dialogue(4)
    rng = numpy.random.default_rng(4)
    cues = sorted((float(start), float(start) + 2, "x") for start in rng.uniform(0, starts[-1], 300))
    outcome = summarize("eng-text", *judge(cues, {"kind": "eng-text", "starts": starts, "ends": ends}))
    assert confidence(outcome) == (0, "none")
