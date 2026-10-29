# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path

import numpy
import pytest

from subtitle_guard import files, storage
from subtitle_guard.errors import UnsafePathError
from subtitle_guard.guard import evaluate_and_fix
from subtitle_guard.subtitles import parse_subtitle, timestamp, write_subtitle

LINES = 150
SRT = "".join(f"{number}\n{timestamp(number * 3.0)} --> {timestamp(number * 3.0 + 1)}\nline {number}\n\n" for number in range(1, LINES + 1))


def test_temporary_names_are_unpredictable_and_a_swapped_target_is_never_written_through(tmp_path):
    subtitle, target = tmp_path / "movie.fr.srt", tmp_path / "victim.txt"
    subtitle.write_text(SRT)
    target.write_text("untouched")
    document = parse_subtitle(str(subtitle))
    subtitle.unlink()
    subtitle.symlink_to(target)
    with pytest.raises(UnsafePathError):
        write_subtitle(str(subtitle), document, [(start + 1, end + 1, text) for start, end, text in document["cues"]])
    assert target.read_text() == "untouched"
    assert not [name for name in os.listdir(tmp_path) if name.endswith(files.TEMPORARY_SUFFIX)]


def test_a_parent_folder_symlink_cannot_escape_the_roots(tmp_path, monkeypatch):
    library, outside = tmp_path / "library", tmp_path / "outside"
    library.mkdir()
    outside.mkdir()
    (outside / "victim.srt").write_text("untouched")
    (library / "Season 1").symlink_to(outside)
    monkeypatch.setattr(files, "ROOTS", [str(library.resolve())])
    with pytest.raises(UnsafePathError):
        files.replace_file(str(library / "Season 1" / "victim.srt"), b"attacker")
    with pytest.raises(UnsafePathError):
        files.read_regular(str(library / "Season 1" / "victim.srt"))
    assert (outside / "victim.srt").read_text() == "untouched"


def test_a_symlinked_subtitle_is_refused(tmp_path):
    real, link = tmp_path / "real.srt", tmp_path / "movie.fr.srt"
    real.write_text(SRT)
    link.symlink_to(real)
    with pytest.raises(UnsafePathError):
        parse_subtitle(str(link))
    with pytest.raises(UnsafePathError):
        files.replace_file(str(link), b"x")
    assert evaluate_and_fix("/missing.mkv", str(link), True)["reason"].startswith("refused:")


def test_a_rewrite_keeps_the_file_mode(tmp_path):
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)
    os.chmod(subtitle, 0o640)
    files.replace_file(str(subtitle), b"new")
    assert oct(os.stat(subtitle).st_mode & 0o777) == "0o640" and subtitle.read_bytes() == b"new"


def test_same_named_subtitles_never_share_backups_and_backups_never_overwrite(tmp_path):
    first = tmp_path / "disk1" / "Movies" / "X (2020)" / "X.en.srt"
    second = tmp_path / "disk2" / "Movies" / "X (2020)" / "X.en.srt"
    assert storage.backup_root(str(first)) != storage.backup_root(str(second))
    first.parent.mkdir(parents=True)
    first.write_text("one")
    folders = {storage.backup_path(str(first)) for _ in range(5)}
    assert len(folders) == 5
    folder = folders.pop()
    files.save_version(str(first), folder, "original")
    with pytest.raises(FileExistsError):
        files.save_version(str(first), folder, "original")


def test_revert_uses_the_latest_original(tmp_path):
    subtitle = tmp_path / "Show" / "Season 1" / "ep.fr.srt"
    subtitle.parent.mkdir(parents=True)
    for content in ("first download", "second download"):
        subtitle.write_text(content)
        files.save_version(str(subtitle), storage.backup_path(str(subtitle)), "original")
    subtitle.write_text("fixed")
    assert Path(storage.revert_source(str(subtitle))).read_text() == "second download"


def test_a_subtitle_changed_during_the_check_is_not_overwritten(tmp_path, monkeypatch):
    from subtitle_guard import guard
    starts = numpy.cumsum(numpy.random.default_rng(3).uniform(1.5, 6.0, LINES))
    late = "".join(f"{number}\n{timestamp(start)} --> {timestamp(start + 1)}\nline {number}\n\n" for number, start in enumerate(starts, 1))
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(late)
    real_judge = guard.judge

    def judge_while_bazarr_replaces_the_file(cues, reference):
        subtitle.write_text(late.replace("line 1\n", "LINE 1\n", 1))
        return real_judge(cues, reference)
    monkeypatch.setattr(guard, "judge", judge_while_bazarr_replaces_the_file)
    monkeypatch.setattr(guard, "load_reference", lambda video, hearing_impaired, count: {
        "kind": "eng-text", "starts": starts + 7, "ends": starts + 8, "index": 2, "image": False})
    outcome = evaluate_and_fix("/movie.mkv", str(subtitle), True)
    assert outcome["decision"] == "unverified" and "changed during the check" in outcome["reason"]
    assert "LINE 1" in subtitle.read_text()


def test_a_same_size_change_with_the_mtime_put_back_is_still_seen(tmp_path):
    import time

    from subtitle_guard.storage import file_signature
    subtitle = tmp_path / "movie.fr.srt"
    subtitle.write_text(SRT)
    before = os.stat(subtitle)
    signature = file_signature(str(subtitle))
    time.sleep(0.01)
    subtitle.write_text(SRT.replace("line 1\n", "LINE 1\n", 1))
    os.utime(subtitle, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(subtitle).st_size == before.st_size and file_signature(str(subtitle)) != signature


def test_a_folder_that_became_a_link_after_the_check_is_never_written_through(tmp_path, monkeypatch):
    library, outside = tmp_path / "library", tmp_path / "outside"
    (library / "Movie").mkdir(parents=True)
    outside.mkdir()
    monkeypatch.setattr(files, "ROOTS", [os.path.realpath(library)])
    subtitle = library / "Movie" / "Movie.fr.srt"
    subtitle.write_text("x")
    checked = os.path.realpath(library / "Movie")
    (library / "Movie").rename(library / "Moved")
    (library / "Movie").symlink_to(outside)
    monkeypatch.setattr(files, "confined", lambda path: (checked, "Movie.fr.srt"))
    with pytest.raises(UnsafePathError):
        files.replace_file(str(subtitle), b"y")
    with pytest.raises(UnsafePathError):
        files.remove_file(str(subtitle))
    assert list(outside.iterdir()) == []
