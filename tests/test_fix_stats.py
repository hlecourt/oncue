# SPDX-License-Identifier: GPL-3.0-or-later
from oncue import reports, stats


def test_the_dashboard_is_computed_once_until_a_new_check_is_reported(tmp_path, monkeypatch):
    subtitle = str(tmp_path / "Movie.fr.srt")
    reports.report(subtitle, "subdl", {"decision": "kept", "confidence": 98, "confidence_band": "high"})
    computed, measured = [], []
    summarize, measure = stats.summarize_period, stats.folder_bytes
    monkeypatch.setattr(stats, "summarize_period", lambda days, today: computed.append(days) or summarize(days, today))
    monkeypatch.setattr(stats, "folder_bytes", lambda folder: measured.append(folder) or measure(folder))
    first = stats.dashboard(days=30)
    assert stats.dashboard(days=30) == first and computed == [30] and len(measured) == 2
    reports.report(subtitle, "ui", {"decision": "deleted", "reason": "deleted by hand"})
    second = stats.dashboard(days=30)
    assert computed == [30, 30] and second["checks"] == 2 and second["subtitles"] == 0 and len(measured) == 2
