# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import subprocess

import pytest

from subtitle_guard import files, reference
from subtitle_guard.audio import audio_fix
from subtitle_guard.errors import ProbeError, UnsafePathError
from subtitle_guard.reference import MEDIA_INPUT_OPTIONS, probe_json

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


def test_without_allowed_folders_nothing_is_written_but_a_dry_run_still_reads(tmp_path, monkeypatch):
    subtitle = tmp_path / "Movie.fr.srt"
    subtitle.write_bytes(b"original")
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [])
    with pytest.raises(UnsafePathError, match="SUBTITLE_GUARD_ROOTS"):
        files.replace_file(str(subtitle), b"changed")
    with pytest.raises(UnsafePathError, match="SUBTITLE_GUARD_ROOTS"):
        files.remove_file(str(subtitle))
    assert subtitle.read_bytes() == b"original" and files.read_regular(str(subtitle)) == b"original"


def test_a_parent_folder_swapped_for_a_link_after_the_check_is_never_read_through(tmp_path, monkeypatch):
    season, outside = tmp_path / "library" / "Season 1", tmp_path / "outside"
    season.mkdir(parents=True)
    outside.mkdir()
    (season / "Movie.fr.srt").write_bytes(b"subtitle")
    (outside / "Movie.fr.srt").write_bytes(b"secret")

    def swap_after_the_check(path):
        season.rename(tmp_path / "library" / "moved")
        season.symlink_to(outside)
        return True
    monkeypatch.setattr(files, "inside_allowed_folders", swap_after_the_check)
    with pytest.raises(UnsafePathError):
        files.read_regular(str(season / "Movie.fr.srt"))


def test_a_video_outside_the_allowed_folders_never_reaches_ffprobe(tmp_path, monkeypatch):
    library, outside = tmp_path / "library", tmp_path / "outside"
    library.mkdir()
    outside.mkdir()
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [os.path.realpath(library)])

    def never(command, timeout):
        raise AssertionError("ffprobe should not run")
    monkeypatch.setattr(reference, "run_tool", never)
    with pytest.raises(ProbeError, match="outside SUBTITLE_GUARD_ROOTS"):
        probe_json(["ffprobe", str(outside / "Movie.mkv")], str(outside / "Movie.mkv"))


@needs_ffmpeg
def test_ffmpeg_reads_only_the_supported_containers(tmp_path):
    for extension in ("mkv", "mp4"):
        video = tmp_path / f"Movie.{extension}"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc", "-t", "1", "-y", str(video)], check=True)
        assert probe_json(["ffprobe", "-v", "error", *MEDIA_INPUT_OPTIONS, "-show_entries", "format=format_name", "-of", "json", str(video)], str(video))
    disguised = tmp_path / "Disguised.mkv"
    disguised.write_text(f"ffconcat version 1.0\nfile '{tmp_path / 'Movie.mkv'}'\n")
    with pytest.raises(ProbeError, match="whitelist"):
        probe_json(["ffprobe", "-v", "error", *MEDIA_INPUT_OPTIONS, "-show_entries", "format=format_name", "-of", "json", str(disguised)], str(disguised))


@needs_ffmpeg
def test_ffsubsync_never_runs_on_a_file_our_ffprobe_refused(tmp_path, monkeypatch):
    disguised, subtitle = tmp_path / "Disguised.mkv", tmp_path / "Disguised.fr.srt"
    disguised.write_text("ffconcat version 1.0\nfile '/etc/passwd'\n")
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nline\n")

    def never(*arguments, **options):
        raise AssertionError("ffsubsync should not run")
    monkeypatch.setattr("subtitle_guard.audio.run_ffsubsync", never)
    outcome, timings = audio_fix(str(disguised), str(subtitle), {"cues": [(1.0, 2.0, "line")]})
    assert outcome["decision"] == "unverified" and outcome["reason"].startswith("could not read the video") and timings is None
