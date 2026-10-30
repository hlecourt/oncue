# SPDX-License-Identifier: GPL-3.0-or-later
from oncue import reports, review_actions
from oncue.errors import ProbeError
from oncue.files import file_hash
from oncue.subtitles import parse_subtitle

SRT = "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n2\n00:00:03,000 --> 00:00:04,000\nworld\n"


def test_a_save_whose_result_cannot_be_measured_is_still_recorded(tmp_path, monkeypatch):
    subtitle = tmp_path / "Movie.fr.srt"
    subtitle.write_text(SRT)

    def unreadable_video(video, hearing_impaired, count):
        raise ProbeError("ffprobe timed out")

    monkeypatch.setattr(review_actions, "load_reference", unreadable_video)
    outcome = review_actions.retime(str(tmp_path / "Movie.mkv"), str(subtitle), file_hash(str(subtitle)), False, [(0, 1, 1.0)])
    assert outcome["decision"] == "manual" and outcome["reference"] == "" and outcome["confidence"] == 0
    assert parse_subtitle(str(subtitle))["cues"][0][0] == 2.0
    assert [row["decision"] for row in reports.report_history(str(subtitle))] == ["manual"]
