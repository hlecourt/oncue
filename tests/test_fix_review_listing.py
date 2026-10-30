# SPDX-License-Identifier: GPL-3.0-or-later
from oncue import reports, review
from oncue.api.presentation import review_reason


def test_a_forced_subtitle_is_recorded_but_stays_out_of_review(tmp_path):
    forced, full = tmp_path / "Movie.fr.forced.srt", tmp_path / "Movie.fr.srt"
    for path in (forced, full):
        path.write_text("x")
        reports.report(str(path), "scan", {"decision": "unverified", "reason": "forced subtitle, too few lines to check"})
    assert {row["subtitle"] for row in reports.latest_reports()} == {str(forced), str(full)}
    assert [row["subtitle"] for row in review.review_items()] == [str(full)]


def test_an_unwritten_sure_fix_reads_the_same_in_review_as_everywhere_else():
    row = {"decision": "fixed", "applied": 0, "note": "", "band": "high", "weak_minutes": ""}
    assert review_reason(row, {"kind": "aligned"}) == "Sure fix found, not written (report mode)"
    assert review_reason({**row, "decision": "proposed", "applied": 1, "band": "medium"}, {"kind": "aligned"}) == "A fix from the video's subtitles is ready"
