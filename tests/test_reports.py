import os

from subtitle_guard import reports, storage


def test_the_old_report_file_is_imported_once_and_bad_rows_are_skipped(tmp_path):
    subtitle = tmp_path / "ep.fr.srt"
    subtitle.write_text("x")
    os.makedirs(storage.STATE_DIRECTORY, exist_ok=True)
    rows = [
        ["2026-10-27T10:00:00", "fixed", "eng-text", "0.4", "0.98", "0.1", "0.2", "3", "2,3", "", "92/high", "", "opensubtitles", str(subtitle)],
        ["2026-10-27T11:00:00", "kept", "eng-text", "0.9", "0.9", "0.1", "", "", "", "", "broken", "", "scan", str(subtitle)],
        ["2026-10-27T12:00:00", "kept", "eng-text", "0.9", "0.9", "0.1", "", "", "", "", "88/high", "{not json", "scan", str(subtitle)],
        ["2026-10-26T09:00:00", "audio-synced", "", "", "", "", "", "", "", "synced to the audio", "check", str(subtitle)],
        ["truncated"],
    ]
    with open(storage.state_path("report.tsv"), "w") as handle:
        handle.write("".join("\t".join(row) + "\n" for row in rows))
    history = reports.report_history()
    assert [row["decision"] for row in history] == ["audio-synced", "fixed"] and history[1]["confidence"] == "92" and history[1]["band"] == "high"
    assert not os.path.exists(storage.state_path("report.tsv")) and os.path.exists(storage.state_path("report.tsv.imported"))
    assert len(reports.report_history()) == 2


def test_a_tab_or_newline_in_a_file_name_cannot_break_the_history(tmp_path):
    strange = tmp_path / "bad\tname\nwith.fr.srt"
    strange.write_text("x")
    reports.report(str(strange), "scan", {"decision": "kept", "confidence": 90, "confidence_band": "high"})
    assert [row["subtitle"] for row in reports.latest_reports()] == [str(strange)]
    assert reports.has_report(str(strange)) and not reports.has_report(str(tmp_path / "other.srt"))


def test_a_dry_run_that_would_fix_is_recorded_and_needs_a_look(tmp_path):
    subtitle = tmp_path / "ep.fr.srt"
    subtitle.write_text("x")
    reports.report(str(subtitle), "scan", {"decision": "fixed", "confidence": 95, "confidence_band": "high"}, applied=False)
    row = reports.latest_reports()[0]
    assert row["applied"] == 0 and reports.needs_review(row)


def test_a_corrupt_state_file_is_set_aside_and_logged(tmp_path):
    from pathlib import Path

    from subtitle_guard import logs, state
    path = state.state_file("/media/Ep.mkv", "/media/Ep.fr.srt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text("{broken")
    assert state.load_state("/media/Ep.mkv", "/media/Ep.fr.srt") == {}
    assert Path(path + ".corrupt").read_text() == "{broken"
    assert logs.recent_events()[0]["event"] == "state.corrupt"
