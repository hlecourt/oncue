# SPDX-License-Identifier: GPL-3.0-or-later
import builtins
import json
import subprocess
import sys
import time

import numpy
import pytest
from test_text_check import dialogue, embed, moved, safe_when_supported

from oncue import configuration, guard, seam_audio, speech_check, text_check, text_confidence, text_models


@pytest.fixture(autouse=True)
def stand_in_safety(monkeypatch):
    monkeypatch.setattr(text_confidence, "passage_safety", safe_when_supported)
    # the small-move mechanics are tested at a passed gate; the shipped models have none (test below)
    monkeypatch.setattr(text_models, "FINE_GATES", {"e5": {"prominence": 5.0, "risk": 0.015}})


def speech_under(cues):
    # one run of speech per line, starting where its truth says the line starts (the baseline is how far official tracks sit from the detected onset)
    return numpy.array([start - speech_check.BASELINE for start, _, _ in cues])


def test_a_mask_gives_the_starts_of_speech_after_real_silence():
    mask = numpy.zeros(100 * 200, dtype=bool)
    for second in range(2, 182, 3):
        mask[second * 100:second * 100 + 100] = True
    onsets = speech_check.onsets_of(mask)
    assert onsets[:2].tolist() == [2.0, 5.0] and len(onsets) == 60


def test_too_little_speech_or_no_mask_gives_no_onsets():
    assert speech_check.onsets_of(None) is None
    assert speech_check.onsets_of(numpy.zeros(1000, dtype=bool)) is None


def test_the_measure_finds_a_known_shift_and_says_how_clearly():
    cues = dialogue(300)
    onsets = speech_under(cues)
    for shift in (0.0, 0.5, -0.75):
        found = speech_check.measure([start + shift for start, _, _ in cues], onsets)
        assert found["offset"] == pytest.approx(shift, abs=0.02) and speech_check.settled(found)


def test_lines_unrelated_to_the_speech_settle_nothing():
    rng = numpy.random.default_rng(1)
    cues = dialogue(300)
    found = speech_check.measure(sorted(rng.uniform(0, 900, 300)), speech_under(cues))
    assert not speech_check.settled(found, speech_check.FILE_PROMINENCE)


def test_too_few_lines_give_no_measure():
    cues = dialogue(100)
    assert speech_check.measure([start for start, _, _ in cues[:3]], speech_under(cues)) is None
    assert speech_check.measure([start for start, _, _ in cues], None) is None


def test_a_rolling_window_off_the_speech_is_found():
    cues = dialogue(300)
    onsets = speech_under(cues)
    assert not speech_check.off_windows([start for start, _, _ in cues], onsets)
    shifted = [start for start, _, _ in moved(cues, 100, 60, 1.0)]
    assert speech_check.off_windows(shifted, onsets)


def test_without_webrtcvad_the_check_gives_no_opinion(monkeypatch):
    real_import = builtins.__import__

    def refuse(name, *arguments, **options):
        if name == "webrtcvad":
            raise ImportError(name)
        return real_import(name, *arguments, **options)
    monkeypatch.setattr(builtins, "__import__", refuse)
    assert speech_check.read_onsets("video.mkv", "eng") is None


def test_an_unreadable_video_gives_no_opinion():
    pytest.importorskip("webrtcvad")
    assert speech_check.read_onsets("/does/not/exist.mkv", "eng") is None


def test_an_enabled_check_with_no_audio_never_verifies_a_file():
    cues = dialogue()
    assert text_check.decide(cues, cues, embed)["file_verified"]
    verdict = text_check.decide(cues, cues, embed, speech_check_on=True, onsets=None)
    assert not verdict["file_verified"] and verdict["verdict"] == text_check.VERDICT["unsettled"] and verdict["timings"] is None


def test_a_file_on_the_speech_is_verified_with_the_check_on():
    cues = dialogue(300)
    verdict = text_check.decide(cues, cues, embed, speech_check_on=True, onsets=speech_under(cues))
    assert verdict["file_verified"] and verdict["verdict"] == text_check.VERDICT["confirmed"]


def test_a_small_offset_of_the_whole_file_is_written_where_the_speech_is_under_it():
    reference_lines = dialogue(300)
    late = [(start + 0.75, end + 0.75, text) for start, end, text in reference_lines]
    verdict = text_check.decide(late, reference_lines, embed, speech_check_on=True, onsets=speech_under(reference_lines))
    assert verdict["verdict"] == text_check.VERDICT["corrected"]
    assert sum(abs(new[0] - right[0]) < 0.15 for new, right in zip(verdict["timings"], reference_lines, strict=True)) >= 0.9 * len(reference_lines)


def test_edge_lines_of_a_small_move_without_speech_under_them_stay_in_place():
    reference_lines = dialogue(300)
    late = [(start + 0.75, end + 0.75, text) for start, end, text in reference_lines]
    onsets = speech_under(reference_lines[3:])
    verdict = text_check.decide(late, reference_lines, embed, speech_check_on=True, onsets=onsets)
    assert verdict["timings"][:3] == late[:3]
    assert abs(verdict["timings"][3][0] - reference_lines[3][0]) < 0.15


def test_a_small_passage_offset_needs_the_speech_to_measure_the_same_move():
    reference_lines = dialogue(300)
    late = moved(reference_lines, 120, 40, 0.8)
    assert text_check.decide(late, reference_lines, embed)["timings"] is None
    assert text_check.decide(late, reference_lines, embed, speech_check_on=True, onsets=speech_under(late))["timings"] is None


def test_a_small_passage_offset_is_written_back_with_the_rest_untouched():
    reference_lines = dialogue(300)
    verdict = text_check.decide(moved(reference_lines, 120, 40, 0.8), reference_lines, embed, speech_check_on=True, onsets=speech_under(reference_lines))
    assert verdict["verdict"] == text_check.VERDICT["corrected"]
    assert all(abs(new[0] - right[0]) < 0.15 for new, right in zip(verdict["timings"], reference_lines, strict=True))


def test_a_file_on_the_speech_is_not_pulled_onto_a_leading_reference():
    cues = dialogue(300)
    leading = [(start - 0.7, end - 0.7, text) for start, end, text in cues]
    verdict = text_check.decide(cues, leading, embed, speech_check_on=True, onsets=speech_under(cues))
    assert verdict["timings"] is None


def test_a_move_away_from_the_speech_is_refused_and_not_even_offered():
    reference_lines = dialogue(300)
    late = [(start + 30, end + 30, text) for start, end, text in reference_lines]
    onsets = speech_under(late)
    verdict = text_check.decide(late, reference_lines, embed, speech_check_on=True, onsets=onsets)
    assert verdict["timings"] is None and verdict["proposal"] is None


def test_a_slow_drift_is_undone_as_one_line_when_the_speech_holds_it():
    reference_lines = dialogue(400)
    last = reference_lines[-1][0]
    drifted = [(start + 2.0 * start / last, end + 2.0 * start / last, text) for start, end, text in reference_lines]
    verdict = text_check.decide(drifted, reference_lines, embed, speech_check_on=True, onsets=speech_under(reference_lines))
    assert verdict["verdict"] == text_check.VERDICT["corrected"] and verdict["scale"] != 1.0
    assert all(abs(new[0] - right[0]) < 0.2 for new, right in zip(verdict["timings"], reference_lines, strict=True))


def test_a_drift_is_not_read_as_one_without_speech():
    reference_lines = dialogue(400)
    last = reference_lines[-1][0]
    drifted = [(start + 2.0 * start / last, end + 2.0 * start / last, text) for start, end, text in reference_lines]
    assert text_check.decide(drifted, reference_lines, embed)["scale"] == 1.0


def test_a_step_is_not_read_as_a_drift():
    reference_lines = dialogue(400)
    stepped = moved(reference_lines, 200, 200, 2.0)
    verdict = text_check.decide(stepped, reference_lines, embed, speech_check_on=True, onsets=speech_under(reference_lines))
    assert verdict["scale"] == 1.0


def test_the_guard_reads_speech_only_when_the_setting_is_on(monkeypatch):
    cues = dialogue()
    calls = []
    monkeypatch.setattr(configuration, "value", lambda name: {"text_check": True, "text_speech_check": False, "text_model": "m"}.get(name, False))
    monkeypatch.setattr(guard, "reference_texts", lambda video, reference, read_pictures: cues)
    monkeypatch.setattr(guard, "load_model", lambda folder: type("Model", (), {"encode": staticmethod(embed), "family": "e5"})())
    monkeypatch.setattr(speech_check, "read_onsets", lambda video, language: calls.append(video))
    assert guard.text_verdict("video.mkv", {"image": False, "index": 2, "kind": "eng-text"}, cues)["file_verified"]
    assert calls == []
    monkeypatch.setattr(configuration, "value", lambda name: {"text_check": True, "text_speech_check": True, "text_model": "m"}.get(name, False))
    assert not guard.text_verdict("video.mkv", {"image": False, "index": 2, "kind": "eng-text"}, cues)["file_verified"]
    assert calls == ["video.mkv"]


def test_a_reference_off_its_own_audio_is_never_verified_with_the_subtitle_that_follows_it():
    reference_lines = dialogue(300)
    onsets = speech_under(reference_lines)
    for shift in (0.75, 1.0, 2.5, 4.0, 6.0, -2.5):
        shifted = [(start + shift, end + shift, text) for start, end, text in reference_lines]
        verdict = text_check.decide(shifted, shifted, embed, speech_check_on=True, onsets=onsets)
        final = verdict["timings"] or shifted
        on_speech = all(abs(new[0] - right[0]) <= 0.5 for new, right in zip(final, reference_lines, strict=True))
        assert on_speech or not verdict["file_verified"], shift
        assert verdict["verdict"] != text_check.VERDICT["confirmed"], shift


def test_a_video_the_speech_cannot_measure_is_not_verified_on_the_text_alone():
    reference_lines = dialogue(300)
    scattered = numpy.sort(numpy.random.default_rng(5).uniform(0, reference_lines[-1][1], 400))
    verdict = text_check.decide(reference_lines, reference_lines, embed, speech_check_on=True, onsets=scattered)
    assert not verdict["file_verified"] and verdict["verdict"] == text_check.VERDICT["unsettled"]


def test_the_edges_of_a_small_move_need_a_real_pair_of_lines_on_the_speech():
    cues = dialogue(40)
    lines = list(range(30))

    def onsets_at(*indices):
        return numpy.array(sorted(cues[index][0] - 0.8 - speech_check.BASELINE for index in indices))
    assert text_check.speech_edges(cues, lines, -0.8, onsets_at(29)) == []
    assert text_check.speech_edges(cues, lines, -0.8, onsets_at(0)) == []
    assert text_check.speech_edges(cues, lines, -0.8, onsets_at(0, 5, 9)) == []
    assert text_check.speech_edges(cues, lines, -0.8, onsets_at(4, 5, 12, 20, 21, 29)) == lines[4:22]


def test_a_small_move_is_judged_on_the_speech_under_the_lines_it_writes():
    reference_lines = dialogue(300)
    late = [(start + 0.75, end + 0.75, text) for start, end, text in reference_lines]
    verdict = text_check.decide(late, reference_lines, embed, speech_check_on=True, onsets=speech_under(reference_lines[3:]))
    written = [candidate for candidate in verdict["candidates"] if candidate["written"] and candidate["origin"] == "fine"]
    assert written and all(candidate["features"]["fine_lines"] == len(candidate["lines"]) for candidate in written)
    assert all(candidate["features"]["fine_share"] == len(candidate["lines"]) / len(late) for candidate in written)


def test_a_small_move_trimmed_under_a_settled_measure_is_not_written():
    reference_lines = dialogue(300)
    late = moved(reference_lines, 120, 40, 0.8)
    heard = reference_lines[:120] + reference_lines[120:140] + reference_lines[160:]
    verdict = text_check.decide(late, reference_lines, embed, speech_check_on=True, onsets=speech_under(heard))
    assert not any(candidate["written"] and candidate["origin"] == "fine" for candidate in verdict["candidates"])


def test_a_small_move_is_written_at_the_safety_its_gate_was_tested_at(monkeypatch):
    features = {"fine_prominence": 9.0}
    monkeypatch.setattr(text_models, "FINE_GATES", {"e5": {"prominence": 6.0, "risk": 0.015}})
    assert text_confidence.fine_safety(features) == pytest.approx(0.985) and text_confidence.fine_safety(features) >= text_confidence.WRITE_SAFETY
    assert text_confidence.fine_safety({"fine_prominence": 5.0}) < text_confidence.WRITE_SAFETY
    monkeypatch.setattr(text_models, "FINE_GATES", {"e5": {"prominence": None, "risk": None}})
    assert text_confidence.WRITE_SAFETY > text_confidence.fine_safety(features) >= text_check.PLAUSIBLE_MOVE
    assert text_confidence.fine_safety(features, "labse") == 0.0


def test_a_stalled_audio_read_ends_at_the_time_limit(monkeypatch):
    stand_in = type(sys)("webrtcvad")
    stand_in.Vad = lambda aggressiveness: type("Vad", (), {"is_speech": staticmethod(lambda frame, rate: True)})()
    monkeypatch.setitem(sys.modules, "webrtcvad", stand_in)
    monkeypatch.setattr(speech_check, "tagged_stream", lambda video, language: 0)
    monkeypatch.setattr(speech_check, "stream_description", lambda video, stream: (2, 0.0))
    monkeypatch.setattr(speech_check, "READ_TIMEOUT_SECONDS", 0.5)
    real_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", lambda command, **options: real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **options))
    started = time.time()
    assert speech_check.read_onsets("video.mkv", "eng") is None
    assert time.time() - started < 10


def test_the_speech_is_read_only_from_a_stream_known_to_be_in_the_reference_language(monkeypatch):
    def probed(languages):
        listing = json.dumps({"streams": [{"index": index, **({"tags": {"language": language}} if language else {})} for index, language in enumerate(languages)]})
        monkeypatch.setattr(seam_audio, "STREAMS", {})
        monkeypatch.setattr(seam_audio.subprocess, "run", lambda *arguments, **options: subprocess.CompletedProcess(arguments, 0, stdout=listing, stderr=""))
    probed(["", "eng"])
    assert seam_audio.tagged_stream("video.mkv", "eng") == 1
    probed(["fre", "ger"])
    assert seam_audio.tagged_stream("video.mkv", "eng") is None and seam_audio.audio_stream("video.mkv", "eng") == 0
    probed(["fre"])
    assert seam_audio.tagged_stream("video.mkv", "eng") is None
    probed(["und"])
    assert seam_audio.tagged_stream("video.mkv", "eng") == 0
    probed(["", ""])
    assert seam_audio.tagged_stream("video.mkv", "") is None
    probed(["fre", "ger"])
    monkeypatch.setitem(sys.modules, "webrtcvad", type(sys)("webrtcvad"))
    assert speech_check.read_onsets("video.mkv", "eng") is None


def test_a_timing_proposal_is_corrected_for_a_leading_reference():
    cues = dialogue(40)
    proposal = [(start + 3.0, end + 3.0, text) if index < 20 else (start, end, text) for index, (start, end, text) in enumerate(cues)]
    led = text_check.led_timings({"vetting": {"reference_lead": -0.3, "onsets": None}}, cues, proposal)
    assert [round(start - old[0], 3) for (start, _, _), old in zip(led, cues, strict=True)] == [3.3] * 20 + [0.0] * 20


def test_a_proposal_off_the_speech_or_with_no_plausible_passage_is_withheld():
    settled = {"offset": 0.0, "prominence": 9.0, "lines": 30}
    off = {"offset": 1.4, "prominence": 9.0, "lines": 30}
    assert text_check.proposal_doubt({"kind": "aligned", "changed": 30, "best_safety": None, "speech_after": off})
    assert not text_check.proposal_doubt({"kind": "aligned", "changed": 30, "best_safety": None, "speech_after": settled})
    assert text_check.proposal_doubt({"kind": "text", "changed": 2, "best_safety": 0.05, "speech_after": None})
    assert not text_check.proposal_doubt({"kind": "text", "changed": 2, "best_safety": 0.6, "speech_after": None})


def test_with_no_small_move_gate_passed_a_small_move_is_offered_never_written(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(text_confidence, "passage_safety", safe_when_supported)
    assert text_models.FINE_GATES["e5"]["prominence"] is None
    reference_lines = dialogue(300)
    verdict = text_check.decide(moved(reference_lines, 120, 40, 0.8), reference_lines, embed, speech_check_on=True, onsets=speech_under(reference_lines))
    assert verdict["timings"] is None and verdict["proposal"] is not None
    assert any(candidate["origin"] == "fine" and not candidate["written"] for candidate in verdict["candidates"])
