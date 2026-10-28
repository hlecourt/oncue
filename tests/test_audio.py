# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import subprocess

from subtitle_guard import audio
from subtitle_guard.outcome import CHANGED_REASON, DECISION
from subtitle_guard.storage import file_signature
from subtitle_guard.subtitles import parse_subtitle, timestamp

SRT = "".join(f"{number}\n{timestamp(number * 4.0)} --> {timestamp(number * 4.0 + 2)}\nline {number}\n\n" for number in range(1, 40))


def fake_ffsubsync(offsets, calls, on_run=None):
    def run_tool(command, seconds):
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


def test_an_audio_sync_is_applied_with_a_backup(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)
    calls = []
    monkeypatch.setattr(audio, "run_tool", fake_ffsubsync([2.5], calls))
    outcome = audio.sync_to_audio(str(tmp_path / "movie.mkv"), str(subtitle), file_signature(str(subtitle)))
    assert outcome["decision"] == DECISION["audio_synced"] and "offset +2.50s" in outcome["reason"]
    assert parse_subtitle(str(subtitle))["cues"][0][0] == 6.5
    assert len(calls) == 1 and "--no-fix-framerate" not in calls[0]
    assert not os.listdir(audio.state_path("audio-work"))


def test_an_offset_at_the_edge_is_retried_without_framerate_and_refused_when_still_large(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)
    calls = []
    monkeypatch.setattr(audio, "run_tool", fake_ffsubsync([115.0, 45.0], calls))
    outcome = audio.sync_to_audio(str(tmp_path / "movie.mkv"), str(subtitle), file_signature(str(subtitle)))
    assert outcome["decision"] == DECISION["unverified"] and "not confident" in outcome["reason"]
    assert "--no-fix-framerate" in calls[1] and subtitle.read_text() == SRT


def test_a_subtitle_changed_during_the_sync_is_left_alone(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)
    signature = file_signature(str(subtitle))
    newer = SRT.replace("line 1\n", "LINE 1\n", 1)
    monkeypatch.setattr(audio, "run_tool", fake_ffsubsync([2.5], [], on_run=lambda: subtitle.write_text(newer)))
    outcome = audio.sync_to_audio(str(tmp_path / "movie.mkv"), str(subtitle), signature)
    assert outcome["reason"] == CHANGED_REASON and subtitle.read_text() == newer


def test_a_failed_run_or_an_unsupported_format_changes_nothing(tmp_path, monkeypatch):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)
    monkeypatch.setattr(audio, "run_tool", lambda command, seconds: subprocess.CompletedProcess(command, 1, "", "Traceback: no audio stream"))
    assert audio.sync_to_audio(str(tmp_path / "movie.mkv"), str(subtitle), file_signature(str(subtitle)))["decision"] == DECISION["unverified"]
    assert subtitle.read_text() == SRT
    picture = tmp_path / "movie.fr.sup"
    picture.write_bytes(b"PG")
    assert "not supported" in audio.sync_to_audio(str(tmp_path / "movie.mkv"), str(picture), file_signature(str(picture)))["reason"]
