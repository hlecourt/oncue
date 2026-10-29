# SPDX-License-Identifier: GPL-3.0-or-later
import os
import time

import numpy
import pytest

from subtitle_guard import bazarr, files, guard, review, state, storage
from subtitle_guard.catalog import proposal_for
from subtitle_guard.files import file_hash
from subtitle_guard.outcome import DECISION
from subtitle_guard.subtitles import parse_subtitle, timestamp

LINES = 150
STARTS = numpy.cumsum(numpy.random.default_rng(3).uniform(1.5, 6.0, LINES))
LATE = "".join(f"{number}\n{timestamp(start)} --> {timestamp(start + 1)}\nline {number}\n\n" for number, start in enumerate(STARTS, 1))


@pytest.fixture
def late_subtitle(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(LATE)
    monkeypatch.setattr(guard, "load_reference", lambda video, hearing_impaired, count: {
        "kind": "eng-text", "starts": STARTS + 7, "ends": STARTS + 8, "index": 2, "image": False})
    return subtitle


def test_a_fix_below_high_confidence_is_never_written_and_waits_as_a_proposal(late_subtitle, monkeypatch):
    monkeypatch.setattr(guard, "confidence", lambda outcome: (65, "medium"))
    outcome = guard.evaluate_and_fix("/movie.mkv", str(late_subtitle), True)
    assert outcome["decision"] == DECISION["proposed"] and outcome["proposed"] == DECISION["fixed"]
    assert late_subtitle.read_text() == LATE and not review.has_backup(str(late_subtitle))
    proposal = proposal_for(str(late_subtitle), file_hash(str(late_subtitle)))
    assert proposal["kind"] == "aligned" and proposal["confidence"] == 65
    applied = review.apply_proposal("/movie.mkv", str(late_subtitle))
    assert applied["decision"] == DECISION["approved"]
    assert numpy.max(numpy.abs(numpy.array([start for start, _, _ in parse_subtitle(str(late_subtitle))["cues"]]) - (STARTS + 7))) < 0.25


def test_a_high_confidence_fix_is_written_with_a_backup(late_subtitle, monkeypatch):
    monkeypatch.setattr(guard, "confidence", lambda outcome: (92, "high"))
    outcome = guard.evaluate_and_fix("/movie.mkv", str(late_subtitle), True)
    assert outcome["decision"] == DECISION["fixed"] and review.has_backup(str(late_subtitle))
    assert numpy.max(numpy.abs(numpy.array([start for start, _, _ in parse_subtitle(str(late_subtitle))["cues"]]) - (STARTS + 7))) < 0.25
    assert proposal_for(str(late_subtitle), file_hash(str(late_subtitle))) is None


def test_report_only_writes_nothing_but_keeps_the_fix_ready(late_subtitle, monkeypatch):
    monkeypatch.setattr(guard, "confidence", lambda outcome: (92, "high"))
    outcome = guard.evaluate_and_fix("/movie.mkv", str(late_subtitle), False)
    assert outcome["decision"] == DECISION["fixed"] and late_subtitle.read_text() == LATE and not review.has_backup(str(late_subtitle))
    assert proposal_for(str(late_subtitle), file_hash(str(late_subtitle)))["kind"] == "aligned"


def test_a_check_that_writes_nothing_makes_no_backup(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(LATE)
    monkeypatch.setattr(guard, "load_reference", lambda video, hearing_impaired, count: {
        "kind": "eng-text", "starts": numpy.sort(numpy.random.default_rng(9).uniform(0, 900, LINES)), "ends": numpy.zeros(LINES), "index": 2, "image": False})
    outcome = guard.evaluate_and_fix("/movie.mkv", str(subtitle), True)
    assert outcome["decision"] == DECISION["rejected"] and not os.path.exists(storage.backup_root(str(subtitle)))


@pytest.mark.parametrize(("outcome", "replace"), [
    ({"decision": "fixed", "confidence_band": "high", "reference": "eng-text"}, False),
    ({"decision": "kept", "confidence_band": "high", "reference": "eng-text"}, False),
    ({"decision": "proposed", "confidence_band": "medium", "reference": "eng-text"}, True),
    ({"decision": "kept", "confidence_band": "medium", "reference": "eng-text"}, True),
    ({"decision": "rejected", "confidence_band": "none"}, True),
    ({"decision": "wrong-episode", "confidence_band": "none", "reference": "eng-text"}, True),
    ({"decision": "proposed", "confidence_band": "low", "reference": "audio"}, True),
    ({"decision": "unverified", "confidence_band": "none", "reference": "audio"}, True),
    ({"decision": "unverified", "confidence_band": "none"}, False),
])
def test_bazarr_is_asked_for_another_subtitle_whenever_a_download_is_not_trusted(outcome, replace):
    assert bazarr.wants_replacement(outcome) is replace


def test_a_proposal_from_bazarr_asks_for_a_replacement_and_keeps_a_copy_until_the_loop_ends(tmp_path, monkeypatch):
    video, subtitle = tmp_path / "Movie.mkv", tmp_path / "Movie.fr.srt"
    video.write_bytes(b"v")
    subtitle.write_text(LATE)
    requested = []
    monkeypatch.setattr(bazarr, "evaluate_and_fix", lambda video, subtitle, apply, neighbours: {
        "decision": "proposed", "confidence_band": "medium", "reference": "eng-text", "after": {"matched": 0.7}})
    monkeypatch.setattr(bazarr, "request_another", lambda video, subtitle, queued: requested.append(subtitle))
    bazarr.handle_download(str(video), str(subtitle), "fr", "opensubtitles", "42", "7")
    recorded = state.load_state(str(video), str(subtitle))
    assert requested == [str(subtitle)] and recorded["pending_retry"]["provider"] == "opensubtitles"
    assert os.path.exists(recorded["attempts"][0]["copy"])
    monkeypatch.setattr(bazarr, "evaluate_and_fix", lambda video, subtitle, apply, neighbours: {
        "decision": "fixed", "confidence_band": "high", "reference": "eng-text", "after": {"matched": 0.95}})
    subtitle.write_text(LATE.replace("line 1\n", "LINE 1\n", 1))
    bazarr.handle_download(str(video), str(subtitle), "fr", "podnapisi", "43", "7")
    assert "pending_retry" not in state.load_state(str(video), str(subtitle)) and not os.path.exists(bazarr.attempt_folder(str(subtitle)))


def test_backups_keep_the_first_version_the_last_ones_and_anything_recent(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(LATE)
    monkeypatch.setattr(files, "KEEP_VERSIONS", 2)
    folders = []
    for _ in range(6):
        folders.append(files.save_backup(str(subtitle), "original"))
    old = time.time() - 40 * 86400
    for folder in folders[:5]:
        os.utime(folder, (old, old))
    files.prune_backups(str(subtitle))
    assert [os.path.exists(folder) for folder in folders] == [True, False, False, False, True, True]


def test_a_video_without_a_reference_track_still_gets_replacements_and_marks_the_audio_as_its_reference(tmp_path, monkeypatch):
    from subtitle_guard import audio
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(LATE)
    monkeypatch.setattr(guard, "load_reference", lambda video, hearing_impaired, count: None)
    monkeypatch.setattr(audio, "audio_fix", lambda video, subtitle, document: (
        {"decision": "unverified", "reason": "no usable subtitle track embedded in the video, audio sync not confident"}, None))
    monkeypatch.setattr(guard, "audio_fix", audio.audio_fix)
    outcome = guard.evaluate_and_fix("/movie.mkv", str(subtitle), True)
    assert outcome["reference"] == "audio" and bazarr.wants_replacement(outcome)


def test_the_best_version_among_audio_tries_is_the_most_confident_one(tmp_path, monkeypatch):
    video, subtitle = tmp_path / "Movie.mkv", tmp_path / "Movie.fr.srt"
    video.write_bytes(b"v")
    subtitle.write_text("third")
    copies = []
    for name in ("first", "second"):
        copy = tmp_path / f"{name}.srt"
        copy.write_text(name)
        copies.append(str(copy))
    monkeypatch.setattr(bazarr, "evaluate_and_fix", lambda video, subtitle, apply, neighbours: {"decision": "proposed", "confidence": 30})
    bazarr.restore_best(str(video), str(subtitle), {"attempts": [{"subtitle": str(subtitle), "confidence": 30, "copy": copies[0]},
                                                                {"subtitle": str(subtitle), "confidence": 55, "copy": copies[1]}]})
    assert subtitle.read_text() == "second"


def test_a_state_folder_that_cannot_be_written_never_stops_a_tool(tmp_path, monkeypatch):
    from subtitle_guard import tools
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(tools, "state_path", lambda *parts: str(blocker / "tool-groups" / parts[-1]))
    assert tools.run_tool(["true"], 5).returncode == 0 and not tools.ACTIVE_GROUPS
