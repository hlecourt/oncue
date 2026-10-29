# SPDX-License-Identifier: GPL-3.0-or-later
from contextlib import closing
from datetime import date

import pytest
from fastapi.testclient import TestClient

from subtitle_guard import jobs, reports, stats
from subtitle_guard.api.app import create_app

TODAY = date.today()


def part(shift, lines, first=10.0):
    return {"first": first, "last": first + 60, "shift": shift, "lines": lines}


@pytest.fixture
def history(tmp_path):
    folder = tmp_path / "Movie (2020)"
    folder.mkdir()
    paths = {name: str(folder / name) for name in ("Movie.2020.fr.srt", "Movie.2020.en.hi.srt", "Movie.2020.pt-br.srt", "Show.S01E01.de.srt", "Old.es.srt")}
    for path in paths.values():
        open(path, "w").close()
    fr, en, pt, de, old = paths.values()
    reports.report(fr, "opensubtitlescom", {"decision": "kept", "confidence": 98, "confidence_band": "high", "reference": "eng-text", "seconds": 0.4})
    reports.report(en, "opensubtitlescom", {"decision": "fixed", "confidence": 100, "confidence_band": "high", "reference": "eng-image", "seconds": 1.2,
                                            "changes": {"scale": 1.0, "lines": 40, "parts": [part(-2.0, 30), part(-0.5, 10, 300)]}})
    reports.report(en, "manual", {"decision": "manual", "confidence": 90, "confidence_band": "high", "reference": "eng-image",
                                  "changes": {"scale": 1.0, "lines": 40, "parts": [part(0.3, 5)]}})
    reports.report(pt, "scan", {"decision": "fixed", "confidence": 100, "confidence_band": "high", "reference": "audio", "seconds": 3.9,
                                "changes": {"scale": 1.0, "lines": 20, "parts": [part(1.5, 20)]}}, applied=False)
    reports.report(pt, "subdl", {"decision": "proposed", "confidence": 63, "confidence_band": "medium", "reference": "eng-text",
                                 "changes": {"scale": 1.0, "lines": 20, "parts": [part(-7.0, 20)]}})
    reports.report(de, "scan", {"decision": "fixed", "confidence": 100, "confidence_band": "high", "reference": "", "seconds": 0.2,
                                "changes": {"scale": 23.976 / 25, "lines": 300, "parts": []}})
    reports.report(old, "subdl", {"decision": "rejected", "confidence": 0, "confidence_band": "none", "reference": "eng-text"})
    with closing(jobs.connect()) as connection:
        connection.execute("update reports set time = '2020-01-01T10:00:00' where subtitle = ?", (old,))
    return paths


def test_the_numbers_count_what_they_say(history):
    view = stats.dashboard(days=30)
    assert view["checks"] == 6 and view["subtitles"] == 4
    assert view["fixed_on_their_own"] == 2 and view["found_not_written"] == 1
    assert view["lines_realigned"] == 40 + 5 + 300
    assert view["undone"] == 1
    assert view["median_offset"] == pytest.approx(2.0)


def test_confidence_counts_only_the_fixes_found_and_offsets_say_late_or_early(history):
    view = stats.dashboard(days=30)
    assert sum(view["confidence"]) == 4 and view["confidence"][19] == 3 and view["confidence"][12] == 1
    late = {(entry["low"], entry["high"]): entry["count"] for entry in view["offsets"]}
    assert late[(2, 5)] == 1 and late[(5, 12)] == 1 and late[(-2, -1)] == 1 and sum(late.values()) == 3


def test_providers_are_only_the_sources_that_sent_a_subtitle(history):
    view = stats.dashboard(days=30)
    by_name = {entry["provider"]: entry for entry in view["providers"]}
    assert set(by_name) == {"opensubtitlescom", "subdl"}
    assert by_name["opensubtitlescom"] == {"provider": "opensubtitlescom", "downloads": 2, "in_sync": 1, "sure_fix": 1, "review": 0, "rejected": 0,
                                           "unchecked": 0, "median_offset": 2.0}
    assert by_name["subdl"]["review"] == 1 and by_name["subdl"]["downloads"] == 1
    assert stats.dashboard()["providers"][1]["downloads"] == 2


def test_languages_references_speed_fixes_and_check_times(history):
    view = stats.dashboard(days=30)
    assert {entry["name"]: entry["count"] for entry in view["languages"]} == {"fr": 1, "en": 1, "pt-br": 1, "de": 1}
    assert {entry["name"]: entry["count"] for entry in view["references"]} == {"text": 2, "picture": 1, "none": 1}
    assert view["speed_fixes"] == [{"name": "25 / 23.976 fps", "count": 1}]
    assert view["check_time"]["median"] == pytest.approx(0.8) and view["check_time"]["bins"][0] == 2 and view["check_time"]["bins"][7] == 1
    assert view["records"]["largest_offset"] == 2.0 and view["records"]["most_parts"] == 2 and view["records"]["fastest_check"] == 0.2
    assert view["records"]["edits_by_hand"] == 1


def test_the_heatmap_covers_26_weeks_ending_today(history):
    days = stats.dashboard(days=30)["per_day"]
    assert len(days) == 26 * 7 and days[-1] == {"day": TODAY.isoformat(), "checks": 6}
    assert sum(stats.dashboard(days=30)["weekly"]) == 6


def test_the_api_sends_the_dashboard_for_a_period(history):
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    view = client.get("/api/stats", params={"days": 30}).json()
    assert view["checks"] == 6 and view["providers"][0]["provider"] == "opensubtitlescom"
    assert client.get("/api/stats").json()["checks"] == 7
    assert client.get("/api/stats", params={"days": 0}).status_code == 422
