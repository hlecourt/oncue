# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import subprocess

import numpy
import pytest

from oncue import audio
from oncue.outcome import DECISION
from oncue.subtitles import parse_subtitle, timestamp

SRT = "".join(f"{number}\n{timestamp(number * 4.0)} --> {timestamp(number * 4.0 + 2)}\nline {number}\n\n" for number in range(1, 40))
LONG_SRT = "".join(f"{number}\n{timestamp(number * 4.0)} --> {timestamp(number * 4.0 + 2)}\nline {number}\n\n" for number in range(1, 80))


@pytest.fixture(autouse=True)
def extracted_soundtrack(monkeypatch):
    monkeypatch.setattr(audio, "extract_soundtrack", lambda video, soundtrack: None)


def fake_ffsubsync(offsets, calls, on_run=None, speech=None):
    def run_tool(command, seconds):
        if command[2] == audio.SPEECH_SCRIPT:
            if speech is None:
                return subprocess.CompletedProcess(command, 1, "", "no speech detector")
            numpy.save(command[-1], speech)
            return subprocess.CompletedProcess(command, 0, "", "")
        calls.append(command)
        offset = offsets[len(calls) - 1]
        output = command[command.index("-o") + 1]
        source = command[command.index("-i") + 1]
        cues = parse_subtitle(source)["cues"]
        with open(output, "w") as handle:
            handle.write("".join(f"{number}\n{timestamp(start + offset)} --> {timestamp(end + offset)}\n{text}\n\n" for number, (start, end, text) in enumerate(cues, 1)))
        if on_run:
            on_run()
        result = {"offset_seconds": offset, "framerate_scale_factor": 1.0, "sync_was_successful": True}
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")
    return run_tool


def test_an_audio_sync_is_never_written_on_its_own_and_waits_as_a_proposal(tmp_path, monkeypatch):
    from oncue import guard, review_actions, storage
    from oncue.catalog import proposal_for
    from oncue.files import file_hash
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(LONG_SRT)
    calls = []
    monkeypatch.setattr(guard, "load_reference", lambda video, hearing_impaired, count: None)
    monkeypatch.setattr(audio, "run_tool", fake_ffsubsync([2.5], calls))
    outcome = guard.evaluate_and_fix(str(tmp_path / "movie.mkv"), str(subtitle), True)
    assert outcome["decision"] == DECISION["proposed"] and outcome["proposed"] == DECISION["audio_synced"] and outcome["confidence_band"] == "low"
    assert subtitle.read_text() == LONG_SRT and len(calls) == 1 and "--no-fix-framerate" not in calls[0]
    proposal = proposal_for(str(subtitle), file_hash(str(subtitle)))
    assert proposal["kind"] == "audio" and proposal["timings"][0][0] == 6.5
    assert not os.listdir(audio.state_path("audio-work"))
    monkeypatch.setattr(review_actions, "load_reference", lambda video, hearing_impaired, count: None)
    applied = review_actions.retime(str(tmp_path / "movie.mkv"), str(subtitle), file_hash(str(subtitle)), True, [])
    assert applied["decision"] == DECISION["approved"] and parse_subtitle(str(subtitle))["cues"][0][0] == 6.5 and storage.backup_folders(str(subtitle))
    assert proposal_for(str(subtitle), file_hash(str(subtitle))) is None


def test_a_proposal_for_an_older_version_of_the_file_is_never_applied(tmp_path, monkeypatch):
    from oncue import guard, review_actions, storage
    from oncue.errors import EditRefusedError
    from oncue.files import file_hash
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(LONG_SRT)
    monkeypatch.setattr(guard, "load_reference", lambda video, hearing_impaired, count: None)
    monkeypatch.setattr(audio, "run_tool", fake_ffsubsync([2.5], []))
    guard.evaluate_and_fix(str(tmp_path / "movie.mkv"), str(subtitle), True)
    seen = file_hash(str(subtitle))
    newer = LONG_SRT.replace("line 1\n", "LINE 1\n", 1)
    subtitle.write_text(newer)
    with pytest.raises(EditRefusedError):
        review_actions.retime(str(tmp_path / "movie.mkv"), str(subtitle), seen, True, [])
    assert subtitle.read_text() == newer and not storage.backup_folders(str(subtitle))


def test_an_offset_at_the_edge_is_retried_without_framerate_and_refused_when_still_large(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)
    calls = []
    monkeypatch.setattr(audio, "run_tool", fake_ffsubsync([115.0, 45.0], calls))
    outcome, timings = audio.audio_fix(str(tmp_path / "movie.mkv"), str(subtitle), parse_subtitle(str(subtitle)))
    assert outcome["decision"] == DECISION["unverified"] and "not confident" in outcome["reason"] and timings is None
    assert "--no-fix-framerate" in calls[1] and subtitle.read_text() == SRT


def test_a_failed_run_or_an_unsupported_format_gives_no_timings(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)
    monkeypatch.setattr(audio, "run_tool", lambda command, seconds: subprocess.CompletedProcess(command, 1, "", "Traceback: no audio stream"))
    outcome, timings = audio.audio_fix(str(tmp_path / "movie.mkv"), str(subtitle), parse_subtitle(str(subtitle)))
    assert outcome["decision"] == DECISION["unverified"] and timings is None and subtitle.read_text() == SRT
    picture = tmp_path / "movie.fr.sup"
    picture.write_bytes(b"PG")
    assert "not supported" in audio.audio_fix(str(tmp_path / "movie.mkv"), str(picture), {"cues": []})[0]["reason"]



def speech_for(cues, length_seconds, noise_seed=4):
    rng = numpy.random.default_rng(noise_seed)
    mask = rng.random(int(length_seconds * audio.SPEECH_FRAMES_PER_SECOND)) < 0.15
    for start, end, _ in cues:
        mask[int(start * 100):int(end * 100)] = True
    return mask


VARIED = [(start, start + 1.2, f"line {number}") for number, start in enumerate(numpy.cumsum(numpy.random.default_rng(5).uniform(2, 7, 200)), 1)]


def test_a_sync_whose_parts_all_land_on_speech_is_trusted_and_one_off_by_a_second_is_not():
    speech = speech_for(VARIED, VARIED[-1][1] + 10)
    right = audio.part_agreement(VARIED, speech)
    assert right["parts"] == 8 and right["agreeing"] == 8
    late = audio.part_agreement([(start + 1.0, end + 1.0, text) for start, end, text in VARIED], speech)
    assert late["agreeing"] == 0 and all(abs(residual + 1.0) <= 0.05 for residual in late["residuals"])
    half = audio.part_agreement([(start + (0.8 if start > 500 else 0), end + (0.8 if start > 500 else 0), text) for start, end, text in VARIED], speech)
    assert 0 < half["agreeing"] < 8


@pytest.mark.parametrize(("agreement", "score", "band"), [
    ({"parts": 8, "agreeing": 8}, 100, "high"), ({"parts": 8, "agreeing": 7}, 88, "high"), ({"parts": 8, "agreeing": 6}, 75, "medium"),
    ({"parts": 3, "agreeing": 3}, 30, "low"), (None, 30, "low"),
])
def test_audio_confidence_is_the_share_of_parts_that_agree(agreement, score, band):
    from oncue.confidence import confidence
    assert confidence({"decision": DECISION["audio_synced"], "agreement": agreement}) == (score, band)


def test_a_confident_audio_sync_is_still_a_proposal_while_automatic_audio_writes_are_off(tmp_path, monkeypatch):
    from oncue import guard
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text("".join(f"{number}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n\n" for number, (start, end, text) in enumerate(VARIED, 1)))
    shifted = [(start + 2.5, end + 2.5, text) for start, end, text in VARIED]
    monkeypatch.setattr(guard, "load_reference", lambda video, hearing_impaired, count: None)
    monkeypatch.setattr(audio, "run_tool", fake_ffsubsync([2.5, 2.5], [], speech=speech_for(shifted, shifted[-1][1] + 10)))
    outcome = guard.evaluate_and_fix(str(tmp_path / "movie.mkv"), str(subtitle), True)
    assert outcome["confidence_band"] == "high" and outcome["decision"] == DECISION["proposed"]
    monkeypatch.setenv("ONCUE_AUDIO_AUTOMATIC", "1")
    subtitle.write_text(subtitle.read_text())
    outcome = guard.evaluate_and_fix(str(tmp_path / "movie.mkv"), str(subtitle), True)
    assert outcome["decision"] == DECISION["audio_synced"] and abs(parse_subtitle(str(subtitle))["cues"][0][0] - shifted[0][0]) < 0.01


def test_the_calibration_counts_confident_results_that_are_wrong(tmp_path, monkeypatch):
    from oncue import calibration
    video, subtitle = tmp_path / "Film.mkv", tmp_path / "Film.en.srt"
    video.write_bytes(b"v")
    subtitle.write_text("x")
    monkeypatch.setattr(calibration, "library_items", lambda root: [(str(video), str(subtitle), [])])
    monkeypatch.setattr(calibration, "true_timings", lambda video, subtitle: VARIED)
    answers = iter([({"decision": DECISION["audio_synced"], "agreement": {"parts": 8, "agreeing": 8}}, VARIED),
                    ({"decision": DECISION["audio_synced"], "agreement": {"parts": 8, "agreeing": 8}}, VARIED),
                    ({"decision": DECISION["audio_synced"], "agreement": {"parts": 8, "agreeing": 1}}, VARIED[::-1])])
    monkeypatch.setattr(calibration, "audio_fix", lambda video, copy, document: next(answers))
    report = calibration.calibrate_audio(str(tmp_path))
    assert report["count"] == 3 and report["confident"] == 2 and report["confident_and_correct"] == 1 and report["false_positives"] == 1
    assert os.listdir(calibration.state_path("calibration")) and subtitle.read_text() == "x"


def test_lines_ffsubsync_drops_are_kept_since_the_timings_come_from_its_offset_and_scale(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)

    def run_tool(command, seconds):
        if command[2] == audio.SPEECH_SCRIPT:
            return subprocess.CompletedProcess(command, 1, "", "no speech detector")
        with open(command[command.index("-o") + 1], "w") as handle:
            handle.write("1\n00:00:06,500 --> 00:00:08,500\nline 1\n")
        return subprocess.CompletedProcess(command, 0, json.dumps({"offset_seconds": 2.5, "framerate_scale_factor": 1.0, "sync_was_successful": True}), "")
    monkeypatch.setattr(audio, "run_tool", run_tool)
    outcome, timings = audio.audio_fix(str(tmp_path / "movie.mkv"), str(subtitle), parse_subtitle(str(subtitle)))
    assert outcome["decision"] == DECISION["audio_synced"] and len(timings) == 39 and timings[-1][0] == 39 * 4.0 + 2.5


def test_unrelated_lines_that_happen_to_sit_near_speech_do_not_count_as_agreeing():
    speech = speech_for(VARIED, VARIED[-1][1] + 10)
    duration = VARIED[-1][1] + VARIED[0][0]
    unrelated = sorted((duration - end, duration - start, text) for start, end, text in VARIED)
    assert audio.part_agreement(unrelated, speech)["agreeing"] <= 1
