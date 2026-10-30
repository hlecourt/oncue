# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil

import pytest
from media import video_with_reference, write_subtitle_file

from subtitle_guard import jobs, reports, review, review_actions, state, storage
from subtitle_guard.files import file_hash

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


@pytest.fixture
def movie(tmp_path):
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    return video, subtitle, starts, ends


@needs_ffmpeg
def test_manual_shift_fixes_a_stretch_and_keeps_a_backup(movie):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    outcome = review_actions.shift_range(str(video), str(subtitle), 123, 183, -3)
    assert outcome["decision"] == "manual" and not outcome["weak_minutes"] and outcome["confidence_band"] == "high"
    assert any(name.startswith("before-manual") for _, _, names in os.walk(storage.backup_root(str(subtitle))) for name in names)
    assert reports.latest_reports()[0]["decision"] == "manual"


@needs_ffmpeg
def test_accepted_subtitles_leave_the_review_list_until_they_change(movie):
    _video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    reports.report(str(subtitle), "scan", {"decision": "kept", "weak_minutes": [2], "confidence": 70, "confidence_band": "medium"})
    assert [item["subtitle"] for item in review.review_items()] == [str(subtitle)]
    review_actions.accept(str(subtitle))
    assert review.review_items() == []
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 1)
    os.utime(subtitle, (os.stat(subtitle).st_atime, os.stat(subtitle).st_mtime + 5))
    assert [item["subtitle"] for item in review.review_items()] == [str(subtitle)]


@needs_ffmpeg
def test_asking_for_another_needs_a_bazarr_attempt(movie, monkeypatch):
    video, subtitle, starts, ends = movie
    write_subtitle_file(subtitle, starts, ends, lambda value: value)
    assert not review_actions.ask_for_another(str(video), str(subtitle))
    monkeypatch.setenv("SUBTITLE_GUARD_QUEUE", "1")
    state.save_state(str(video), str(subtitle), {"attempts": [{"provider": "gestdown", "subtitle_id": "x1", "media_id": 12, "series_id": None,
                                                                 "language": "fr", "subtitle": str(subtitle)}]})
    assert review_actions.ask_for_another(str(video), str(subtitle))
    assert jobs.recent()[0]["kind"] == "blacklist"
    assert state.load_state(str(video), str(subtitle))["pending_replacement"]["media_type"] == "movie"


def test_a_deleted_subtitle_is_backed_up_and_leaves_review(tmp_path):
    from subtitle_guard import reports
    season = tmp_path / "Show" / "Season 1"
    season.mkdir(parents=True)
    wrong = season / "Show.S01E02.fr.srt"
    wrong.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
    reports.report(str(wrong), "opensubtitles", {"decision": "wrong-episode", "matches": "S01E03"})
    review_actions.delete_subtitle(str(wrong), file_hash(str(wrong)))
    assert not wrong.exists()
    saved = [name for _, _, names in os.walk(storage.backup_root(str(wrong))) for name in names]
    assert saved == ["deleted.srt"] and str(wrong) not in [row["subtitle"] for row in reports.latest_reports()]

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
    video, subtitle, _starts, _ends = movie
    _, other_starts, other_ends = video_with_reference(tmp_path / "other", seed=99)
    write_subtitle_file(subtitle, other_starts, other_ends, lambda value: value)
    diagnosis = review.diagnose(str(video), str(subtitle))
    assert diagnosis["state"] == "no-match" and diagnosis["parts"] == []


def test_a_symlinked_video_or_one_outside_the_roots_is_never_used(tmp_path, monkeypatch):
    from subtitle_guard import files
    library, outside = tmp_path / "library", tmp_path / "outside"
    library.mkdir()
    outside.mkdir()
    (outside / "Movie.mkv").write_bytes(b"v")
    (library / "Movie.mkv").symlink_to(outside / "Movie.mkv")
    (library / "Movie.fr.srt").write_text("x")
    assert review.video_for(str(library / "Movie.fr.srt")) is None
    (library / "Movie.mkv").unlink()
    (library / "Movie.mkv").write_bytes(b"v")
    assert review.video_for(str(library / "Movie.fr.srt")) == str(library / "Movie.mkv")
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [str(outside.resolve())])
    assert review.video_for(str(library / "Movie.fr.srt")) is None
    assert os.path.exists(library / "Movie.mkv")
