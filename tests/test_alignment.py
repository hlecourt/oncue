import numpy
import pytest

from subtitle_guard.alignment import judge
from synthetic import dialogue, translation

PAL = 23.976 / 25


def distorted(cues, transform):
    return [(transform(start), transform(end), text) for start, end, text in cues]


def run(cues, truth, reference):
    decision, _, _, attempt = judge(cues, reference)
    timings = cues if decision == "kept" else attempt["timings"]
    errors = numpy.abs(numpy.array([timing[0] for timing in timings]) - numpy.array(truth))
    return decision, float(numpy.mean(errors <= 1.0))


@pytest.fixture(scope="module")
def episode():
    starts, ends = dialogue(1)
    cues = translation(starts, ends, 1)
    return {"kind": "eng-text", "starts": starts, "ends": ends}, cues, [start for start, _, _ in cues]


def cut_after(moment, gap):
    return lambda value: value if value < moment else value + gap


@pytest.mark.parametrize("label, transform", [
    ("offset", lambda value: value + 37.5),
    ("framerate", lambda value: value * PAL - 12),
    ("speed 1 percent", lambda value: value * 1.01),
    ("cut", cut_after(600, -25)),
    ("several cuts", lambda value: cut_after(900, 14)(cut_after(400, -6)(value)) + 3),
])
def test_misaligned_subtitles_are_fixed(episode, label, transform):
    reference, cues, truth = episode
    decision, within_second = run(distorted(cues, transform), truth, reference)
    assert decision == "fixed", label
    assert within_second >= 0.95, label


def test_synced_subtitle_is_kept(episode):
    reference, cues, truth = episode
    assert run(cues, truth, reference)[0] == "kept"


def test_local_shift_inside_a_good_file_is_fixed(episode):
    reference, cues, truth = episode
    decision, within_second = run(distorted(cues, lambda value: value + 3 if 500 <= value < 590 else value), truth, reference)
    assert decision == "fixed"
    assert within_second >= 0.95


def test_offset_beyond_search_range_is_rejected(episode):
    reference, cues, truth = episode
    assert run(distorted(cues, lambda value: value + 1500), truth, reference)[0] == "rejected"


def test_offset_beyond_the_normal_range_is_found_by_the_wide_search(episode):
    reference, cues, truth = episode
    decision, within_second = run(distorted(cues, lambda value: value + 600), truth, reference)
    assert decision == "fixed" and within_second >= 0.95


def test_subtitle_of_another_episode_is_rejected(episode):
    reference, _, _ = episode
    other_starts, other_ends = dialogue(2)
    other = translation(other_starts, other_ends, 2)
    decision, _, _, _ = judge(other, reference)
    assert decision == "rejected"
