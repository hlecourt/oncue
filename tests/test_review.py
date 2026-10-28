import os
import shutil

import pytest

from subtitle_guard import jobs, review, storage
from subtitle_guard.guard import evaluate_and_fix
from media import video_with_reference, write_subtitle_file

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


@pytest.fixture
def movie(tmp_path):
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    return video, subtitle, starts, ends


@needs_ffmpeg
def test_side_by_side_shows_reference_text_and_matches(movie):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    assert review.video_for(str(subtitle)) == str(video)
    view = review.side_by_side(str(video), str(subtitle), 60, 240)
    reference_lines = [line for line in view["lines"] if line[1] == "reference"]
    subtitle_lines = [line for line in view["lines"] if line[1] == "subtitle"]
    assert reference_lines and reference_lines[0][2].startswith("line ")
    shifted = [matched for start, _, _, matched, _ in subtitle_lines if 123 <= start < 183]
    assert shifted and sum(shifted) / len(shifted) <= 0.35
    assert all(matched for start, _, _, matched, _ in subtitle_lines if start < 120 or start >= 185)
    assert 2 in view["measure"]["weak_minutes"]


@needs_ffmpeg
def test_manual_shift_fixes_a_stretch_and_keeps_a_backup(movie):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    outcome = review.shift_range(str(video), str(subtitle), 123, 183, -3)
    assert outcome["decision"] == "manual" and not outcome["weak_minutes"] and outcome["confidence_band"] == "high"
    assert any(name.startswith("before-manual") for _, _, names in os.walk(storage.backup_root(str(subtitle))) for name in names)
    assert storage.latest_reports()[0]["decision"] == "manual"


@needs_ffmpeg
def test_accepted_subtitles_leave_the_review_list_until_they_change(movie):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    storage.report(str(subtitle), "scan", {"decision": "kept", "weak_minutes": [2], "confidence": 70, "confidence_band": "medium"})
    assert [item["subtitle"] for item in review.review_items()] == [str(subtitle)]
    review.accept(str(subtitle))
    assert review.review_items() == []
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 1)
    os.utime(subtitle, (os.stat(subtitle).st_atime, os.stat(subtitle).st_mtime + 5))
    assert [item["subtitle"] for item in review.review_items()] == [str(subtitle)]


@needs_ffmpeg
def test_revert_restores_the_provider_file(movie):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 9)
    provided = subtitle.read_text()
    assert evaluate_and_fix(str(video), str(subtitle), True)["decision"] == "fixed"
    assert subtitle.read_text() != provided
    assert review.revert(str(subtitle))
    assert subtitle.read_text() == provided
    assert storage.latest_reports()[0]["decision"] == "reverted"


@needs_ffmpeg
def test_asking_for_another_needs_a_bazarr_attempt(movie, monkeypatch):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value)
    assert not review.ask_for_another(str(video), str(subtitle))
    monkeypatch.setenv("SUBTITLE_GUARD_QUEUE", "1")
    storage.save_state(str(video), str(subtitle), {"attempts": [{"provider": "gestdown", "subtitle_id": "x1", "media_id": 12, "series_id": None,
                                                                 "language": "fr", "subtitle": str(subtitle)}]})
    assert review.ask_for_another(str(video), str(subtitle))
    assert jobs.recent()[0]["kind"] == "blacklist"
    assert storage.load_state(str(video), str(subtitle))["pending_retry"]["media_type"] == "movie"


def test_a_wrong_episode_subtitle_moves_to_the_episode_it_matches(tmp_path):
    season = tmp_path / "Show" / "Season 1"
    season.mkdir(parents=True)
    for number in range(1, 5):
        (season / f"Show.S01E{number:02d}.mkv").write_text("")
    wrong = season / "Show.S01E02.fr.srt"
    wrong.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
    existing = season / "Show.S01E03.fr.srt"
    existing.write_text("old")
    target, job_id = review.move_to_episode(str(season / "Show.S01E02.mkv"), str(wrong), "S01E03")
    assert target == str(existing) and existing.read_text().startswith("1\n") and not wrong.exists()
    assert jobs.job(job_id)["arguments"]["video"] == str(season / "Show.S01E03.mkv")
    saved = [name for _, _, names in os.walk(storage.backup_root(str(existing))) for name in names]
    assert saved == ["before-move.srt"]
    assert review.move_to_episode(str(season / "Show.S01E02.mkv"), str(existing), "S01E09") is None



@needs_ffmpeg
def test_diagnosis_lists_the_out_of_sync_parts_in_plain_terms(movie):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    diagnosis = review.diagnose(str(video), str(subtitle))
    assert diagnosis["state"] == "out-of-sync" and diagnosis["fixable"]
    assert len(diagnosis["parts"]) == 1
    part = diagnosis["parts"][0]
    moved = [start + 3 for start in starts if 120 <= start < 180]
    assert abs(part["shift"] + 3.0) <= 0.2 and abs(part["first"] - moved[0]) < 0.01 and abs(part["last"] - moved[-1]) < 0.01
    assert part["lines"] == len(moved)
    assert review.diagnose(str(video), str(subtitle)) == diagnosis
    write_subtitle_file(subtitle, starts, ends, lambda value: value)
    os.utime(subtitle, (os.stat(subtitle).st_atime, os.stat(subtitle).st_mtime + 5))
    assert review.diagnose(str(video), str(subtitle))["state"] == "in-sync"


@needs_ffmpeg
def test_a_subtitle_of_another_film_is_not_reported_as_shifted_parts(movie, tmp_path):
    video, subtitle, starts, ends = movie
    _, other_starts, other_ends = video_with_reference(tmp_path / "other", seed=99)
    write_subtitle_file(subtitle, other_starts, other_ends, lambda value: value)
    diagnosis = review.diagnose(str(video), str(subtitle))
    assert diagnosis["state"] == "no-match" and diagnosis["parts"] == []


@needs_ffmpeg
def test_each_moment_pairs_the_video_line_with_the_subtitle_line_that_belongs_to_it(movie):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    diagnosis = review.diagnose(str(video), str(subtitle))
    moments = review.side_by_side(str(video), str(subtitle), 120, 180, diagnosis["shifts"])["moments"]
    paired = [moment for moment in moments if moment["video"] and moment["subtitle"]]
    assert len(paired) >= 0.9 * len([moment for moment in moments if moment["video"]])
    assert all(moment["video"].split()[-1] == moment["subtitle"].split()[-1] for moment in paired)
    assert sum(moment["shift"] < -2.5 for moment in paired) >= 0.8 * len(paired)
