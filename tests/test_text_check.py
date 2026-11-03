# SPDX-License-Identifier: GPL-3.0-or-later
import itertools
import json
import struct

import numpy
import pytest

from oncue import configuration, guard, reference, reference_text, reports, subtitles, text_align, text_check, text_confidence, text_model, text_models
from oncue.confidence import confidence
from oncue.outcome import DECISION, FIX_KIND

WORDS = ("rain", "door", "coffee", "train", "letter", "garden", "doctor", "money", "beach", "music", "winter", "window", "dinner", "phone",
         "river", "school", "bridge", "candle", "forest", "planet", "ticket", "jacket", "mirror", "island", "pocket", "castle", "rocket", "bottle")


def embed(lines):
    # a stand-in for the sentence model: lines sharing words get similar vectors, so a line matches the reference line it repeats
    vectors = numpy.zeros((len(lines), len(WORDS) * len(WORDS)))
    for row, line in enumerate(lines):
        words = [word for word in line.lower().strip(".").split() if word in WORDS]
        for first, second in itertools.pairwise(words):
            vectors[row, WORDS.index(first) * len(WORDS) + WORDS.index(second)] = 1.0
    norms = numpy.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / numpy.where(norms == 0, 1, norms)


def dialogue(count=120, seed=3):
    rng = numpy.random.default_rng(seed)
    moments = numpy.cumsum(rng.uniform(2.0, 4.5, count)) + 10
    lines = [" ".join(rng.choice(WORDS, 4, replace=False)) for _ in range(count)]
    return [(float(start), float(start) + 1.8, line) for start, line in zip(moments, lines, strict=True)]


def with_pauses(cues, at=(50, 56), seconds=12.0):
    # pauses in the dialogue before line 50 and after line 55, so a passage can be off without passing its neighbours, as in a real file
    return [(start + seconds * sum(index >= mark for mark in at), end + seconds * sum(index >= mark for mark in at), text)
            for index, (start, end, text) in enumerate(cues)]


def moved(cues, first, count, shift):
    return [(start + shift, end + shift, text) if first <= index < first + count else (start, end, text) for index, (start, end, text) in enumerate(cues)]


def safe_when_supported(features, family="e5"):
    # a stand-in for the calibrated passage model: a passage of three lines or more, mostly anchored, with no contradiction
    return 1.0 if features["lines"] >= 3 and features["anchored_share"] >= 0.5 and features["contradictions"] == 0 else 0.0


@pytest.fixture(autouse=True)
def stand_in_safety(monkeypatch):
    monkeypatch.setattr(text_confidence, "passage_safety", safe_when_supported)


def track_of(reference_lines):
    return {"kind": "eng-text", "starts": numpy.array([start for start, _, _ in reference_lines]), "ends": numpy.array([end for _, end, _ in reference_lines])}


def test_a_subtitle_whose_lines_sit_on_the_lines_they_translate_is_verified():
    cues = dialogue()
    verdict = text_check.decide(cues, cues, embed)
    assert verdict["verdict"] == text_check.VERDICT["confirmed"] and verdict["file_verified"] and verdict["doubtful_minutes"] == []


def test_a_short_passage_off_by_eight_seconds_is_written_back_in_place():
    reference_lines = with_pauses(dialogue())
    verdict = text_check.decide(moved(reference_lines, 50, 6, 8.0), reference_lines, embed)
    assert verdict["verdict"] == text_check.VERDICT["corrected"] and verdict["written"] == 1
    assert [round(start, 3) for start, _, _ in verdict["timings"]] == [round(start, 3) for start, _, _ in reference_lines]


def test_a_whole_subtitle_thirty_seconds_late_is_moved_back_as_one_passage():
    reference_lines = dialogue()
    late = [(start + 30, end + 30, text) for start, end, text in reference_lines]
    verdict = text_check.decide(late, reference_lines, embed)
    assert verdict["verdict"] == text_check.VERDICT["corrected"] and len(verdict["candidates"]) == 1
    assert all(abs(new[0] - right[0]) < 0.01 for new, right in zip(verdict["timings"], reference_lines, strict=True))


def test_a_frame_rate_mismatch_is_found_from_the_anchors_and_undone():
    reference_lines = dialogue(200)
    stretched = [(start * 23.976 / 25, end * 23.976 / 25, text) for start, end, text in reference_lines]
    verdict = text_check.decide(stretched, reference_lines, embed)
    assert verdict["scale"] == pytest.approx(25 / 23.976)
    assert all(abs(new[0] - right[0]) < 0.3 for new, right in zip(verdict["timings"], reference_lines, strict=True))


def test_an_unsafe_candidate_is_only_offered_and_the_guard_keeps_it_out_of_the_automatic_band():
    reference_lines = with_pauses(dialogue(), at=(50, 52))
    pair = moved(reference_lines, 50, 2, 8.0)
    verdict = text_check.decide(pair, reference_lines, embed)
    assert verdict["verdict"] == text_check.VERDICT["unsettled"] and verdict["timings"] is None
    assert [round(start, 3) for start, _, _ in verdict["proposal"]] == [round(start, 3) for start, _, _ in reference_lines]
    timing = {"reference": "eng-text", "before": {}, "decision": DECISION["kept"], "weak_minutes": []}
    outcome, fix = guard.with_text(timing, verdict, pair, track_of(reference_lines), {"version": "v"})
    assert fix["kind"] == FIX_KIND["text"] and confidence(outcome)[0] <= 89
    timed = {**timing, "decision": DECISION["fixed"], "after": {"matched": 0.95, "noise": 0.2, "worst_margin": 0.8, "weak_run": 0.0}}
    outcome, fix = guard.with_text(timed, verdict, pair, track_of(reference_lines), {"version": "v"})
    assert fix is None and outcome["decision"] == DECISION["fixed"] and confidence(outcome)[0] <= 89


def test_without_enough_reference_text_the_check_gives_no_verdict():
    cues = dialogue()
    for reference_lines in (cues[:10], None):
        verdict = text_check.decide(cues, reference_lines, embed)
        assert verdict["verdict"] == text_check.VERDICT["unsettled"] and verdict["timings"] is None and verdict["doubtful_minutes"]


def test_a_written_correction_scores_from_its_safety_and_unsettled_text_is_capped():
    base = {"decision": DECISION["fixed"], "after": {"matched": 0.7, "noise": 0.25, "worst_margin": 0.3, "weak_run": -5.0}}
    written, band = confidence({**base, "text": {"verdict": text_check.VERDICT["corrected"], "safety": 1.0}})
    verified, _ = confidence({**base, "decision": DECISION["kept"], "text": {"verdict": text_check.VERDICT["confirmed"], "verified": 1.0}})
    capped, capped_band = confidence({**base, "after": {"matched": 0.95, "noise": 0.2, "worst_margin": 0.8, "weak_run": 0.0},
                                      "text": {"verdict": text_check.VERDICT["unsettled"]}})
    assert written == 100 and band == "high" and verified == 100
    assert capped == 89 and capped_band == "medium"


def test_a_partial_write_still_lists_what_the_text_could_not_vouch_for_in_review():
    reference_lines = with_pauses(dialogue())
    cues = moved(reference_lines, 50, 6, 8.0)
    cues = [(start, end, "Oui.") if 90 <= index < 98 else (start, end, text) for index, (start, end, text) in enumerate(cues)]
    verdict = text_check.decide(cues, reference_lines, embed)
    timing = {"reference": "eng-text", "before": {}, "decision": DECISION["kept"], "weak_minutes": []}
    outcome, fix = guard.with_text(timing, verdict, cues, track_of(reference_lines), {"version": "v"})
    assert outcome["decision"] == DECISION["fixed"] and fix["kind"] == FIX_KIND["text"] and confidence(outcome)[0] >= 90
    assert {int(reference_lines[index][0] // 60) for index in range(90, 98)} <= set(outcome["weak_minutes"])
    row = {"subtitle": "/media/Show.fr.srt", "applied": 1, "decision": outcome["decision"], "weak_minutes": outcome["weak_minutes"], "band": "high"}
    assert reports.needs_review(row)


def test_the_text_check_stays_off_unless_switched_on(monkeypatch):
    monkeypatch.setattr(guard, "reference_texts", lambda *arguments, **options: pytest.fail("reference text read while the check is off"))
    assert guard.text_verdict("video.mkv", {"image": False, "index": 2}, dialogue()) is None
    monkeypatch.setenv("ONCUE_TEXT_CHECK", "1")
    monkeypatch.setenv("ONCUE_TEXT_MODEL", "/nowhere")
    monkeypatch.setattr(guard, "reference_texts", lambda *arguments, **options: dialogue())
    assert configuration.value("text_check") is True
    assert guard.text_verdict("video.mkv", {"image": False, "index": 2}, dialogue())["verdict"] == text_check.VERDICT["unsettled"]


def test_picture_starts_and_clears_pair_into_lines_shown_until_cleared_or_replaced():
    starts = [10.0, 12.0, 20.0, 40.0]
    clears = [11.5, 25.0]
    assert reference.paired_cues(starts, clears) == [(10.0, 11.5), (12.0, 20.0), (20.0, 25.0), (40.0, 42.0)]
    assert reference.paired_cues([5.0], [30.0]) == [(5.0, 15.0)]


def test_a_picture_track_reads_its_text_from_an_offline_file_first(tmp_path, monkeypatch):
    folder = tmp_path / "state" / reference_text.OFFLINE_FOLDER
    folder.mkdir(parents=True)
    (folder / "Movie.mkv.3.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n2\n00:00:03,000 --> 00:00:04,000\nGood night.\n")
    monkeypatch.setattr(reference_text, "picture_texts", lambda *arguments: pytest.fail("OCR run although an offline text exists"))
    texts = reference_text.reference_texts(str(tmp_path / "Movie.mkv"), {"image": True, "index": 3, "kind": "eng-image"}, read_pictures=True)
    assert texts == [(1.0, 2.0, "Hello there."), (3.0, 4.0, "Good night.")]


def test_without_tesseract_picture_tracks_give_no_text(tmp_path, monkeypatch):
    monkeypatch.setattr(reference_text.shutil, "which", lambda name: None)
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"")
    assert reference_text.reference_texts(str(video), {"image": True, "index": 3, "kind": "eng-image", "starts": [1.0], "ends": [2.0]},
                                          read_pictures=True) is None


def test_ocr_reads_the_frame_in_the_middle_of_each_picture_line(tmp_path, monkeypatch):
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"")
    monkeypatch.setattr(reference_text.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(reference_text, "render_pictures", lambda *arguments: {frame: f"frame-{frame}.png" for frame in range(0, 100)})
    read = []
    monkeypatch.setattr(reference_text, "read_picture", lambda path, language: read.append((path, language)) or f"text of {path}")
    texts = reference_text.reference_texts(str(video), {"image": True, "index": 3, "kind": "fre-image", "starts": [10.0, 20.0], "ends": [12.0, 23.0]},
                                           read_pictures=True)
    assert read == [("frame-22.png", "fra"), ("frame-43.png", "fra")]
    assert texts == [(10.0, 12.0, "text of frame-22.png"), (20.0, 23.0, "text of frame-43.png")]


def test_dense_layers_are_read_from_safetensors_without_torch(tmp_path):
    weight, bias = numpy.arange(6, dtype=numpy.float32).reshape(2, 3), numpy.array([0.5, -0.5], dtype=numpy.float32)
    header = {"linear.weight": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]},
              "linear.bias": {"dtype": "F32", "shape": [2], "data_offsets": [24, 32]}}
    encoded = json.dumps(header).encode()
    (tmp_path / "model.safetensors").write_bytes(struct.pack("<Q", len(encoded)) + encoded + weight.tobytes() + bias.tobytes())
    (tmp_path / "config.json").write_text(json.dumps({"out_features": 2, "activation_function": "torch.nn.modules.activation.Tanh"}))
    loaded_weight, loaded_bias, activation = text_model.dense_layer(str(tmp_path))
    assert numpy.array_equal(loaded_weight, weight) and numpy.array_equal(loaded_bias, bias)
    assert activation(numpy.array([0.0])) == pytest.approx([0.0])


def test_a_folder_that_is_not_a_sentence_model_is_refused(tmp_path):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    with pytest.raises(text_model.ConfigError, match="not a sentence model folder"):
        text_model.SentenceModel(str(tmp_path))


def test_a_line_translating_two_reference_lines_is_not_a_contradiction():
    reference_lines = dialogue()
    first, second = reference_lines[60], reference_lines[61]
    joined = [*reference_lines[:60], (first[0], second[1], f"{first[2]} {second[2]}"), *reference_lines[62:]]
    verdict = text_check.decide(joined, reference_lines, embed)
    assert verdict["verdict"] == text_check.VERDICT["confirmed"] and verdict["contradictions"] == 0


def test_a_passage_the_text_cannot_read_is_neither_verified_nor_written():
    reference_lines = dialogue()
    unreadable = [(start + 6, end + 6, "Oui.") if 70 <= index < 78 else (start, end, text) for index, (start, end, text) in enumerate(reference_lines)]
    verdict = text_check.decide(unreadable, reference_lines, embed)
    assert verdict["verdict"] != text_check.VERDICT["confirmed"]
    assert all((verdict["timings"] or unreadable)[index] == unreadable[index] for index in range(70, 78))


def test_an_enabled_check_without_reference_text_fails_closed(monkeypatch):
    monkeypatch.setenv("ONCUE_TEXT_CHECK", "1")
    monkeypatch.setattr(guard, "reference_texts", lambda *arguments, **options: None)
    verdict = guard.text_verdict("video.mkv", {"image": True, "index": 2}, dialogue())
    timing = {"reference": "eng-image", "before": {}, "decision": DECISION["kept"], "weak_minutes": [],
              "after": {"matched": 0.95, "noise": 0.2, "worst_margin": 0.8, "weak_run": 0.0}}
    outcome, fix = guard.with_text(timing, verdict, dialogue(), {}, {"version": "v"})
    assert fix is None and confidence(outcome) == (89, "medium") and outcome["weak_minutes"]


def test_the_text_never_lifts_a_timing_fix_into_the_automatic_band():
    reference_lines = dialogue()
    timed = {"reference": "eng-text", "before": {}, "decision": DECISION["fixed"], "weak_minutes": [],
             "after": {"matched": 0.95, "noise": 0.2, "worst_margin": 0.8, "weak_run": 0.0}, "objective": 1.0, "null_objective": 0.0}
    unsettled = text_check.decide(moved(reference_lines, 50, 2, 8.0), reference_lines, embed)
    outcome, fix = guard.with_text(timed, unsettled, reference_lines, {}, {"version": "v"})
    assert fix is None and confidence(outcome)[0] <= 89


def test_a_correction_never_drags_an_unreadable_line_that_is_already_in_place():
    reference_lines = dialogue(60)
    late = [(start + 10, end + 10, text) for start, end, text in reference_lines]
    late[30] = (reference_lines[30][0], reference_lines[30][1], "Oui.")
    verdict = text_check.decide(late, reference_lines, embed)
    final = verdict["timings"] or verdict["proposal"]
    assert final[30][0] == reference_lines[30][0]
    assert verdict["file_verified"] is False


def test_one_line_eight_seconds_off_among_exact_matches_is_not_verified():
    reference_lines = dialogue()
    verdict = text_check.decide(moved(reference_lines, 60, 1, 8.0), reference_lines, embed)
    assert verdict["verdict"] != text_check.VERDICT["confirmed"] and verdict["contradictions"] == 1


def test_a_line_slid_onto_its_neighbour_is_offered_back_on_its_own_reference_line():
    reference_lines = dialogue()
    slid = list(reference_lines)
    slid[60] = (reference_lines[61][0], reference_lines[61][1], reference_lines[60][2])
    slid[61] = (reference_lines[61][0] + 1.9, reference_lines[61][1] + 1.9, reference_lines[61][2])
    verdict = text_check.decide(slid, reference_lines, embed)
    single = [candidate for candidate in verdict["candidates"] if candidate["kind"] == "single"]
    assert single and verdict["proposal"][60][0] == pytest.approx(reference_lines[60][0])
    assert not verdict["file_verified"]


def test_untranslated_reference_dialogue_is_reported_with_its_minutes_and_never_filled():
    reference_lines = dialogue()
    missing = reference_lines[:40] + reference_lines[55:]
    verdict = text_check.decide(missing, reference_lines, embed)
    assert len(verdict["gaps"]) == 1
    gap = verdict["gaps"][0]
    assert gap["from"] == pytest.approx(reference_lines[40][0], abs=0.01) and gap["to"] == pytest.approx(reference_lines[54][1], abs=0.01) and gap["lines"] == 15
    assert int(reference_lines[45][0] // 60) in verdict["doubtful_minutes"]
    assert verdict["timings"] is None or len(verdict["timings"]) == len(missing)
    timing = {"reference": "eng-text", "before": {}, "decision": DECISION["kept"], "weak_minutes": []}
    outcome, _ = guard.with_text(timing, verdict, missing, track_of(reference_lines), {"version": "v"})
    assert "reference dialogue without subtitle" in outcome["reason"] and outcome["gaps"]


def test_speech_under_a_seam_line_decides_its_side():
    reference_lines = dialogue()
    cues = moved(reference_lines, 50, 30, 12.0)
    cues = [(start, end, "Hmm.") if index == 50 else (start, end, text) for index, (start, end, text) in enumerate(cues)]
    speech = numpy.zeros(int(cues[-1][1] * 100) + 100, dtype=numpy.float32)
    true_start, true_end = reference_lines[50][0], reference_lines[50][1]
    speech[int(true_start * 100):int(true_end * 100)] = 1.0
    with_speech = text_check.decide(cues, reference_lines, embed, speech=speech)
    assert with_speech["proposal"][50][0] == pytest.approx(true_start)


def test_speech_is_read_only_around_seams():
    reference_lines = dialogue()
    cues = moved(reference_lines, 50, 30, 12.0)
    cues = [(start, end, "Hmm.") if index == 50 else (start, end, text) for index, (start, end, text) in enumerate(cues)]
    asked = []
    text_check.decide(cues, reference_lines, embed, speech_around=asked.append)
    assert asked and all(end - start < 10 for start, end in asked[0])
    assert len(asked[0]) <= 3 * 4


def test_the_scale_is_one_unless_the_anchors_drift_steadily():
    groups = [{"lines": (index,), "references": (index,), "anchor": True} for index in range(60)]
    cues = [(index * 3.0, index * 3.0 + 1, "") for index in range(60)]
    reference_lines = [(index * 3.0 + 0.2 * (index % 2), index * 3.0 + 1, "") for index in range(60)]
    assert text_align.choose_scale(groups, cues, reference_lines) == 1.0


def test_a_sentence_model_without_a_calibration_never_authorises_a_write():
    assert set(text_models.PASSAGE_MODELS) == {"e5"}
    assert text_confidence.calibrated(text_models.PASSAGE_MODELS.get("uncalibrated"), {}) == 0.0


def test_timings_that_cannot_be_written_as_evaluated_stay_a_proposal(monkeypatch):
    saved = []
    monkeypatch.setattr(guard, "save_proposal", lambda *arguments: saved.append(arguments))
    monkeypatch.setattr(guard, "write_subtitle", lambda *arguments: pytest.fail("written although a line starts before zero"))
    cues = dialogue()
    document = {"format": "srt", "cues": cues}
    timings = [(-1.5, 0.5, cues[0][2]), *cues[1:]]
    fix = {"kind": FIX_KIND["text"], "timings": timings, "version": "v", "document": document, "signature": "s"}
    outcome = guard.settle("video.mkv", "/media/Show.fr.srt", {"decision": DECISION["fixed"], "confidence": 95, "confidence_band": "high"}, fix, True)
    assert outcome["decision"] == DECISION["proposed"] and "before the beginning" in outcome["reason"] and saved


def test_a_seam_line_whose_words_repeat_elsewhere_is_settled_by_the_reference_lines_around_each_place():
    # line 50 ("garden doctor") is said again much later: its best match anywhere is far away, but where it is it reads right
    reference_lines = with_pauses(dialogue(), at=(51,))
    reference_lines[50] = (reference_lines[50][0], reference_lines[50][1], "rain garden doctor money")
    reference_lines[110] = (reference_lines[110][0], reference_lines[110][1], "garden doctor")
    cues = moved(reference_lines, 51, 69, 9.0)
    cues[50] = (cues[50][0], cues[50][1], "garden doctor")
    verdict = text_check.decide(cues, reference_lines, embed)
    final = verdict["timings"] or verdict["proposal"]
    assert final[50][0] == pytest.approx(reference_lines[50][0])


def test_lines_after_two_passages_on_the_same_offset_follow_a_whole_file_move():
    reference_lines = dialogue(80)
    late = [(start + 30, end + 30, text) for start, end, text in reference_lines]
    late[40] = (late[40][0], late[40][1], reference_lines[10][2])
    late = [(start, end, "Oui.") if index >= 76 else (start, end, text) for index, (start, end, text) in enumerate(late)]
    verdict = text_check.decide(late, reference_lines, embed)
    final = verdict["timings"] or verdict["proposal"]
    assert all(final[index][0] == pytest.approx(reference_lines[index][0], abs=0.01) for index in range(76, 80))


def jumped_tail():
    # lines 50-79 belong 20 s later; 80-82 too, but they cannot be read, sit past a pause and would be jumped by the passage before them
    reference_lines = with_pauses(with_pauses(dialogue(83), at=(50,), seconds=25.0), at=(80,), seconds=10.0)
    cues = [(start - 20, end - 20, text) if index >= 50 else (start, end, text) for index, (start, end, text) in enumerate(reference_lines)]
    return reference_lines, [(start, end, "Oui.") if index >= 80 else (start, end, text) for index, (start, end, text) in enumerate(cues)]


def test_a_refine_pass_extends_a_passage_over_the_unread_lines_its_move_would_jump():
    reference_lines, cues = jumped_tail()
    first = text_check.decide(cues, reference_lines, embed, passes=1)
    assert first["timings"] is None and first["passes"][0]["written"] == 0
    refined = text_check.decide(cues, reference_lines, embed)
    assert refined["passes"][1]["written"] == 1
    written = [candidate for candidate in refined["candidates"] if candidate["written"]]
    assert written[0]["origin"] == "extended" and set(range(80, 83)) <= set(written[0]["lines"])
    assert all(new[0] == pytest.approx(right[0], abs=0.01) for new, right in zip(refined["timings"], reference_lines, strict=True))


def test_a_refine_pass_never_moves_a_line_an_earlier_pass_wrote_or_verified_in_place():
    reference_lines, cues = jumped_tail()
    cues = moved(cues, 10, 6, 8.0)
    first = text_check.decide(cues, reference_lines, embed, passes=1)
    refined = text_check.decide(cues, reference_lines, embed)
    settled = {line for candidate in first["candidates"] if candidate["written"] for line in candidate["lines"]}
    settled |= {line for line in range(len(cues)) if first["timings"] is None or first["timings"][line][0] == cues[line][0]} - set(range(50, 83))
    assert settled and all(refined["timings"][line][0] == (first["timings"] or cues)[line][0] for line in settled)


def test_a_pass_the_output_check_refuses_is_undone():
    reference_lines, cues = jumped_tail()
    calls = []

    def refuse_extension(timings):
        calls.append(timings)
        return "refused" if timings[82][0] != cues[82][0] else ""
    verdict = text_check.decide(cues, reference_lines, embed, output_check=refuse_extension)
    assert calls and verdict["timings"] is None and verdict["verdict"] != text_check.VERDICT["corrected"]


def test_recognised_words_count_only_where_they_were_read_and_heard():
    words = [(10.0, 10.4, " rain"), (10.4, 10.9, " door"), (10.9, 11.3, " coffee")]
    requests = [(9.8, 11.6, "rain door coffee"), (9.8, 11.6, "garden doctor money"), (30.0, 32.0, "rain door"), (20.0, 21.0, "rain door")]
    agreements = text_check.listen_agreements(requests, words, [(9.0, 12.0), (19.5, 21.5)], embed)
    assert agreements[0] == 1.0 and agreements[1] == -1.0
    assert agreements[2] is None and agreements[3] is None


def test_recognised_speech_at_a_place_settles_a_seam_line_the_text_cannot_read():
    reference_lines, cues = jumped_tail()
    cues = [(start, end, "Oui.") if index in (50, 51) else (start, end, text) for index, (start, end, text) in enumerate(cues)]
    late = {round(reference_lines[index][0], 1) for index in (50, 51)}

    def listen(requests):
        return [1.0 if round(start + text_check.LISTEN_PAD, 1) in late else -1.0 for start, *_ in requests]
    verdict = text_check.decide(cues, reference_lines, embed, listen=listen)
    assert verdict["listened"] > 0


def test_after_a_partial_write_the_remaining_proposal_waits_on_the_written_version(monkeypatch, tmp_path):
    saved = []
    cues = dialogue(30)
    raw = "\n".join(f"{number}\n{text_truth_stamp(start)} --> {text_truth_stamp(end)}\n{text}\n" for number, (start, end, text) in enumerate(cues, 1))
    subtitle = tmp_path / "Show.fr.srt"
    subtitle.write_text(raw)
    document = subtitles.parse_subtitle(str(subtitle))
    monkeypatch.setattr(guard, "save_proposal", lambda *arguments: saved.append(arguments))
    monkeypatch.setattr(guard, "save_backup", lambda *arguments: "backup")
    monkeypatch.setattr(guard, "confined", lambda path: path)
    monkeypatch.setattr(guard, "file_signature", lambda path: "s")
    monkeypatch.setattr(guard, "discard_proposal", lambda path: None)
    timings = [(start + 1, end + 1, text) for start, end, text in document["cues"]]
    remaining = [(start + 0.5, end + 0.5, text) if index < 3 else (start, end, text) for index, (start, end, text) in enumerate(timings)]
    fix = {"kind": FIX_KIND["text"], "timings": timings, "remaining": remaining, "version": "v", "document": document, "signature": "s"}
    outcome = guard.settle("video.mkv", str(subtitle), {"decision": DECISION["fixed"], "confidence": 100, "confidence_band": "high"}, fix, True)
    assert outcome["backup"] == "backup" and len(saved) == 1
    assert saved[0][2] == guard.file_hash(str(subtitle)) != "v" and saved[0][4] == remaining
    assert saved[0][5]["confidence"] <= 89 and saved[0][5]["confidence_band"] == "medium" and saved[0][5]["changes"]["lines"] == len(remaining)


def unsettled_text(cues, proposal, lead=0.0):
    return {"verdict": text_check.VERDICT["unsettled"], "timings": None, "proposal": proposal, "doubtful_minutes": [0], "off_minutes": [], "gaps": [],
            "candidates": [], "reason": "", "vetting": {"onsets": None, "reference_lead": lead}}


def test_a_withheld_proposal_is_not_shown_in_sync_and_the_other_episode_check_still_runs(monkeypatch):
    reference_lines = dialogue()
    cues = moved(reference_lines, 50, 6, 8.0)
    monkeypatch.setattr(text_check, "proposal_doubt", lambda evidence: "the lines it changes would sit off the speech")
    outcome, fix = guard.decide(cues, track_of(reference_lines), unsettled_text(cues, reference_lines))
    assert fix is None and outcome["decision"] == DECISION["rejected"] and outcome["withheld"] and "proposal withheld" in outcome["reason"]
    assert reports.needs_review({"subtitle": "Show.fr.srt", "applied": False, "decision": outcome["decision"], "weak_minutes": "0", "band": "none"})
    monkeypatch.setattr(guard, "parse_subtitle", lambda path: {"cues": cues})
    monkeypatch.setattr(guard, "file_signature", lambda path: "s")
    monkeypatch.setattr(guard, "file_hash", lambda path: "v")
    monkeypatch.setattr(guard, "is_hearing_impaired", lambda path: False)
    monkeypatch.setattr(guard, "load_reference", lambda video, hearing_impaired, count: track_of(reference_lines))
    monkeypatch.setattr(guard, "text_verdict", lambda video, reference, cues, document: unsettled_text(cues, reference_lines))
    monkeypatch.setattr(guard, "find_better_episode", lambda cues, hearing_impaired, neighbours: "S01E03")
    outcome, fix = guard.examine("video.mkv", "/media/Show.fr.srt", True, [("S01E03", "other.mkv")])
    assert fix is None and outcome["decision"] == DECISION["wrong_episode"] and outcome["matches"] == "S01E03"


def test_a_vetoed_proposal_takes_down_the_one_saved_before_on_the_same_version(monkeypatch):
    discarded = []
    monkeypatch.setattr(guard, "discard_proposal", discarded.append)
    guard.settle("video.mkv", "/media/Show.fr.srt", {"decision": DECISION["rejected"], "withheld": True}, None, False)
    guard.settle("video.mkv", "/media/Other.fr.srt", {"decision": DECISION["unverified"]}, None, False)
    assert discarded == ["/media/Show.fr.srt"]


def test_what_a_partial_write_leaves_is_vetted_like_any_proposal(monkeypatch):
    reference_lines = with_pauses(dialogue())
    cues = moved(reference_lines, 50, 6, 8.0)
    written = list(reference_lines)
    remaining = [(start + 3.0, end + 3.0, text) if index < 10 else (start, end, text) for index, (start, end, text) in enumerate(written)]
    text = {**unsettled_text(cues, remaining), "verdict": text_check.VERDICT["corrected"], "timings": written, "file_verified": False,
            "candidates": [{"safety": 0.6, "written": False, "pass": 1}]}
    outcome, fix = guard.decide(cues, track_of(reference_lines), text)
    assert fix["remaining"] == remaining and outcome["remaining_evidence"]["changed"] == 10
    monkeypatch.setattr(text_check, "proposal_doubt", lambda evidence: "no passage it moves is plausible" if evidence["changed"] == 10 else "")
    outcome, fix = guard.decide(cues, track_of(reference_lines), text)
    assert fix["timings"] == written and fix["remaining"] is None and "remaining proposal withheld" in outcome["reason"]


def test_a_timing_proposal_is_described_as_offered_after_the_lead_correction():
    reference_lines = dialogue()
    cues = [(start + 30, end + 30, text) for start, end, text in reference_lines]
    plain, plain_fix = guard.decide(cues, track_of(reference_lines), unsettled_text(cues, None))
    outcome, fix = guard.decide(cues, track_of(reference_lines), unsettled_text(cues, None, lead=-0.4))
    assert fix["kind"] == plain_fix["kind"] == FIX_KIND["aligned"]
    assert all(new[0] - old[0] == pytest.approx(0.4) for new, old in zip(fix["timings"], plain_fix["timings"], strict=True))
    assert outcome["changes"] == guard.describe_changes(cues, {"timings": fix["timings"], "scale": outcome["changes"]["scale"]}) != plain["changes"]


def text_truth_stamp(seconds):
    milliseconds = round(seconds * 1000)
    return f"{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d},{milliseconds % 1000:03d}"


def test_the_guard_and_the_benchmarks_share_one_decision_without_any_io(monkeypatch):
    reference_lines = with_pauses(dialogue())
    cues = moved(reference_lines, 50, 6, 8.0)
    monkeypatch.setattr(guard, "judge", lambda cues, reference: (DECISION["kept"], {"matched": 0.9}, [], {"fixed": False}))
    outcome, fix = guard.decide(cues, track_of(reference_lines), text_check.decide(cues, reference_lines, embed))
    assert outcome["decision"] == DECISION["fixed"] and fix["kind"] == FIX_KIND["text"] and set(fix) >= {"kind", "timings"}
    assert "document" not in fix and "signature" not in fix


def test_untranslated_stretches_come_out_in_the_form_the_evaluation_scores():
    reference_lines = dialogue()
    cues = [cue for index, cue in enumerate(reference_lines) if not 40 <= index < 50]
    timing = {"reference": "eng-text", "before": {}, "decision": DECISION["kept"], "weak_minutes": []}
    outcome, _ = guard.with_text(timing, text_check.decide(cues, reference_lines, embed), cues, track_of(reference_lines), {})
    assert outcome["gaps"] and all(len(gap) == 3 and gap[0] < gap[1] for gap in outcome["gaps"])
    assert tuple(outcome["gaps"][0])[:2] == (outcome["gaps"][0][0], outcome["gaps"][0][1])


def test_review_holds_every_keep_under_the_automatic_band_and_every_unsettled_text_verdict():
    row = {"subtitle": "/media/Show.fr.srt", "applied": 0, "decision": DECISION["kept"], "weak_minutes": "", "band": "medium"}
    assert reports.needs_review(row)
    assert not reports.needs_review({**row, "band": "high"})
    assert reports.needs_review({**row, "band": "high", "text_verdict": "unsettled"})
    assert not reports.needs_review({**row, "band": ""})


def test_the_report_row_keeps_the_text_verdict():
    reports.report("/media/Show.fr.srt", "scan", {"decision": DECISION["kept"], "confidence": 100, "confidence_band": "high",
                                                  "text": {"verdict": text_check.VERDICT["confirmed"]}})
    assert reports.report_history("/media/Show.fr.srt")[0]["text_verdict"] == text_check.VERDICT["confirmed"]


@pytest.mark.parametrize("damage", ["every line 0.8 s late", "every line shown 0.1 s", "last line ends 6 s late", "a sound moved 4 s"])
def test_a_file_off_in_ways_the_text_does_not_place_is_never_verified(damage):
    reference_lines = with_pauses(dialogue(), at=(50, 51), seconds=10.0)
    reference_lines[50] = (reference_lines[50][0], reference_lines[50][1], "[door slams]")
    cues = list(reference_lines)
    if damage == "every line 0.8 s late":
        cues = [(start + 0.8, end + 0.8, text) for start, end, text in cues]
    elif damage == "every line shown 0.1 s":
        cues = [(start, start + 0.1, text) for start, _, text in cues]
    elif damage == "last line ends 6 s late":
        cues[-1] = (cues[-1][0], cues[-1][1] + 6.0, cues[-1][2])
    else:
        cues[50] = (cues[50][0] + 4.0, cues[50][1] + 4.0, cues[50][2])
    assert text_check.decide(list(reference_lines), reference_lines, embed)["file_verified"]
    verdict = text_check.decide(cues, reference_lines, embed)
    assert not verdict["file_verified"] and verdict["verdict"] != text_check.VERDICT["confirmed"] and verdict["doubtful_minutes"]


def test_a_pair_that_already_overlapped_may_not_overlap_further():
    raw = "1\n00:00:00,000 --> 00:00:02,000\nrain door\n\n2\n00:00:01,000 --> 00:00:03,000\ncoffee train\n"
    document = subtitles.parse_subtitle("", raw=raw.encode())
    assert subtitles.output_flaw(document, [(0.0, 8.0, "rain door"), (1.0, 3.0, "coffee train")]) == "line 1 would overlap line 2"
    assert subtitles.output_flaw(document, [(0.0, 2.0, "rain door"), (1.0, 3.0, "coffee train")]) == ""


def test_recognised_words_also_count_against_the_reference_line_in_their_own_language():
    words = [(10.0, 10.4, " rain"), (10.4, 10.9, " door"), (10.9, 11.3, " coffee")]
    agreements = text_check.listen_agreements([(9.8, 11.6, "garden doctor money", "rain door coffee")], words, [(9.0, 12.0)], embed)
    assert agreements == [1.0]


def test_no_file_asks_to_hear_more_than_the_audio_budget():
    reference_lines, cues = jumped_tail()
    cues = [(start, end, "Oui.") if index in (50, 51) else (start, end, text) for index, (start, end, text) in enumerate(cues)]
    asked = []
    text_check.decide(cues, reference_lines, embed, listen=lambda requests: asked.extend(requests) or [None] * len(requests))
    assert sum(end - start for start, end, *_ in asked) <= text_check.MAX_LISTEN_SECONDS


SAFER_WHEN_HIGHER = ("log_lines", "min_seam_margin", "file_share", "coincide_moved", "worst_gain", "text_gain", "file_anchored", "anchored_share",
                     "min_margin", "median_margin", "added_support", "cut_margin")
SAFER_WHEN_LOWER = ("contradictions", "max_contradiction", "seam_lines", "scaled", "coincide_now", "undecided_neighbours", "short", "target_overlap",
                    "source_overlaps", "unsupported_edges", "offset_spread", "worst_deviation", "added_lines", "file_passages", "longest_inside")


KINDS = ("first", "refine")


@pytest.mark.parametrize("kind", KINDS)
def test_the_fitted_safety_model_only_pushes_each_feature_its_safe_way(kind):
    weights = text_models.PASSAGE_MODELS["e5"][kind]["weights"]
    assert all(weights[name] >= 0 for name in SAFER_WHEN_HIGHER if name in weights)
    assert all(weights[name] <= 0 for name in SAFER_WHEN_LOWER if name in weights)


@pytest.mark.parametrize("kind", KINDS)
def test_the_fitted_safety_model_refuses_what_it_was_never_fitted_on(kind):
    model = text_models.PASSAGE_MODELS["e5"][kind]
    typical = dict(model["means"])
    bounded = next(name for name, (_, high) in model["bounds"].items() if high is not None)
    assert text_confidence.calibrated(model, {**typical, bounded: model["bounds"][bounded][1] + 40.0}) == 0.0
    missing = {name: value for name, value in typical.items() if name != next(iter(model["weights"]))}
    assert text_confidence.calibrated(model, missing) == 0.0


def test_a_candidate_is_written_only_past_its_gate_whatever_the_table_says():
    model = {"intercept": 0.0, "weights": {"lines": 1.0}, "means": {"lines": 0.0}, "scales": {"lines": 1.0}, "table": [(0.0, 0.999), (1.0, 0.999)], "gate": 0.9}
    assert text_confidence.calibrated(model, {"lines": -1.0}) < text_confidence.WRITE_SAFETY
    assert text_confidence.calibrated(model, {"lines": 5.0}) >= text_confidence.WRITE_SAFETY


def test_several_passages_lower_the_file_safety_below_the_weakest_one():
    assert text_confidence.file_safety([0.99] * 10) < 0.91
    assert text_confidence.file_safety([]) == 0.0


def real_safety_features(**changes):
    features = {"interleaved": 0.0, "lines": 40.0, "worst_gain": 1.0, "file_share": 0.1, "file_passages": 1.0, **dict.fromkeys(text_confidence.REFINE_ORIGINS, 0.0)}
    return {**features, **changes}


def test_unread_lines_interleaved_through_a_short_move_are_refused_but_not_in_a_whole_file_move():
    assert text_confidence.interleaved_streams(real_safety_features(interleaved=2.0, worst_gain=-1.0))
    assert not text_confidence.interleaved_streams(real_safety_features(interleaved=2.0, worst_gain=-1.0, file_share=0.9))
    assert not text_confidence.interleaved_streams(real_safety_features(interleaved=0.0, worst_gain=-1.0))


def test_a_file_in_a_dozen_pieces_gets_no_automatic_write():
    assert float(numpy.log1p(13)) >= text_confidence.MESSY_FILE_PASSAGES > float(numpy.log1p(8))


def found_for(cues, reference_lines):
    vectors = embed([text_check.clean(text) or "-" for _, _, text in cues])
    reference_vectors = embed([text for _, _, text in reference_lines])
    found = text_align.align(cues, reference_lines, vectors, reference_vectors)
    found.update({"reference_starts": numpy.array([start for start, _, _ in reference_lines]), "reference_ends": numpy.array([end for _, end, _ in reference_lines]),
                  "own_groups": {line: group for group in found["groups"] for line in group["lines"]}, "reference": reference_lines, "trimmed": set()})
    return found


def test_neighbouring_passages_on_practically_one_offset_are_one_move_each_keeping_its_own_offset():
    cues = dialogue(10)
    found = {"lines": [{"how": "anchored", "offset": 0.0}] * 10}
    candidates = text_check.candidate_passages(cues, found, [-5.0] * 5 + [-5.2] * 5, 1.0)
    assert len(candidates) == 1 and text_check.line_offset(candidates[0], 0) == -5.0 and text_check.line_offset(candidates[0], 9) == -5.2
    assert [round(start - cues[line][0], 3) for line, (start, _, _) in enumerate(text_check.apply(cues, candidates, 1.0))] == [-5.0] * 5 + [-5.2] * 5
    assert len(text_check.candidate_passages(cues, found, [-5.0] * 5 + [-6.0] * 5, 1.0)) == 2


def test_a_line_whose_own_match_is_elsewhere_has_no_evidence_for_the_move():
    reference_lines = dialogue()
    found = found_for(reference_lines, reference_lines)
    assert text_check.edge_evidence(reference_lines, found, 40, 0.0, [12.0])
    assert not text_check.edge_evidence(reference_lines, found, 40, 12.0, [0.0])


def test_a_seam_line_nothing_reads_is_trimmed_off_the_passage_beside_lines_in_place():
    # S01E02 line 520, line one past a moved passage: no match of its own, no reference line meeting it at the moved place
    reference_lines = with_pauses(dialogue())
    damaged = moved(reference_lines, 50, 6, 8.0)
    damaged[56] = (damaged[56][0], damaged[56][1], "unrelated words nothing reads")
    found = found_for(damaged, reference_lines)
    assert not text_check.edge_evidence(damaged, found, 56, -8.0, [0.0])


def test_lines_past_the_last_anchor_are_offered_with_the_passage_beside_them():
    cues = dialogue(20)
    base = {"lines": list(range(5, 15)), "offset": -3.0, "moves": {}}
    found = {"passages": [{"first": 5, "last": 14, "offset": -3.0}]}
    offers = text_check.file_edge_lines(cues, found, [None] * 20, [base], set())
    assert {tuple(offer["lines"]) for offer in offers} >= {tuple(range(15, 20)), tuple(range(5))}
    assert all(offer["origin"] == "edge" and offer["offset"] == -3.0 for offer in offers)
    assert text_check.file_edge_lines(cues, found, [None] * 20, [base], {17, 2}) == []


def test_the_time_prior_favours_a_passages_place_and_never_outweighs_strong_text():
    cues = [(10.0, 11.0, "a"), (20.0, 21.0, "b")]
    reference_lines = [(10.1, 11.0, "x"), (15.1, 16.0, "y"), (500.0, 501.0, "z")]
    prior = text_align.time_prior(cues, reference_lines, [{"first": 0, "offset": 0.0}, {"first": 1, "offset": -5.0}], 1.0)
    assert prior[0, 0] == text_align.PRIOR_BONUS and prior[1, 1] == text_align.PRIOR_BONUS
    assert prior[0, 2] == -text_align.PRIOR_WEIGHT and text_align.PRIOR_WEIGHT < text_align.ANCHOR_SCORE - text_align.MATCH_FLOOR + 3.0


def test_two_lines_of_one_sentence_are_recognised():
    lines = [(0.0, 1.0, "so I went to"), (1.2, 2.0, "the river."), (2.2, 3.0, "Then home"), (9.0, 10.0, "at night.")]
    assert text_align.sentence_pairs(lines).tolist() == [1.0, 0.0, 0.0]


def test_when_a_move_breaks_the_order_the_alternative_it_overlapped_gets_its_turn():
    # a cut mid-dialogue: the longest alternative jumps a line left in place, the one it overlapped can still be written
    cues = dialogue(10)
    breaking = {"kind": "passage", "offset": -5.0, "lines": list(range(3, 10)), "moves": {}, "safety": 1.0}
    fitting = {"kind": "passage", "offset": 5.0, "lines": [8, 9], "moves": {}, "safety": 0.99}
    assert [candidate["lines"] for candidate in text_check.select(cues, [], [breaking, fitting], 1.0, None)] == [[8, 9]]


def test_a_refused_output_check_writes_nothing_of_the_pass():
    cues = dialogue(10)
    only = {"kind": "passage", "offset": 40.0, "lines": list(range(10)), "moves": {}, "safety": 1.0}
    assert text_check.select(cues, [], [only], 1.0, lambda timings: "refused") == []


def test_a_candidate_the_output_check_refuses_on_its_own_does_not_take_the_others_down():
    cues = dialogue(60)
    good = {"kind": "passage", "lines": list(range(5, 15)), "offset": 2.0, "safety": 1.0}
    flawed = {"kind": "passage", "lines": list(range(40, 50)), "offset": 2.0, "safety": 1.0}

    def refuses(timings):
        return "flawed" if timings[45][0] != cues[45][0] else ""

    assert text_check.select(cues, [], [good, flawed], 1.0, refuses) == [good]
