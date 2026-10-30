# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import shutil
import subprocess

import pytest

from subtitle_guard import audio, files, preview, reference
from subtitle_guard.errors import ProbeError
from subtitle_guard.outcome import DECISION
from subtitle_guard.subtitles import parse_subtitle, timestamp

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


def make_video(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-y", str(path)],
                   check=True)
    return str(path)


@needs_ffmpeg
def test_a_parent_folder_swapped_for_a_link_after_the_video_was_opened_cannot_redirect_ffprobe(tmp_path, monkeypatch):
    season, outside = tmp_path / "library" / "Season 1", tmp_path / "outside"
    video = make_video(season / "Movie.mkv", 2)
    make_video(outside / "Movie.mkv", 7)
    real_run_tool = reference.run_tool

    def swap_then_run(command, timeout, pass_fds):
        season.rename(tmp_path / "library" / "moved")
        season.symlink_to(outside)
        return real_run_tool(command, timeout, pass_fds)
    monkeypatch.setattr(reference, "run_tool", swap_then_run)
    assert preview.video_duration(video) == pytest.approx(2, abs=0.1)
    monkeypatch.setattr(reference, "run_tool", real_run_tool)
    with pytest.raises(ProbeError, match="link"):
        preview.video_duration(video)


def test_a_video_outside_the_allowed_folders_or_reached_through_a_link_is_refused(tmp_path, monkeypatch):
    library, outside = tmp_path / "library", tmp_path / "outside"
    (library / "Show").mkdir(parents=True)
    outside.mkdir()
    (library / "Show" / "Movie.mkv").write_bytes(b"video")
    (outside / "Movie.mkv").write_bytes(b"video")
    (library / "Linked.mkv").symlink_to(library / "Show" / "Movie.mkv")
    (library / "Linked folder").symlink_to(library / "Show")
    os.mkfifo(library / "Fifo.mkv")
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [os.path.realpath(library)])

    def never(command, timeout, pass_fds):
        raise AssertionError("ffprobe should not run")
    monkeypatch.setattr(reference, "run_tool", never)
    for video, reason in [(outside / "Movie.mkv", "outside SUBTITLE_GUARD_ROOTS"), (library / "Linked.mkv", "link"),
                          (library / "Linked folder" / "Movie.mkv", "link"), (library / "Fifo.mkv", "not a regular file")]:
        with pytest.raises(ProbeError, match=reason):
            preview.video_duration(str(video))


@needs_ffmpeg
def test_ffsubsync_is_never_given_a_path_inside_the_media_folders(tmp_path, monkeypatch):
    library = tmp_path / "library"
    video = make_video(library / "Movie.mkv", 3)
    subtitle = library / "Movie.fr.srt"
    subtitle.write_text("".join(f"{number}\n{timestamp(number * 0.5)} --> {timestamp(number * 0.5 + 0.3)}\nline {number}\n\n" for number in range(1, 5)))
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [os.path.realpath(library)])
    commands = []

    def recorded(command, seconds):
        commands.append((command, os.path.getsize(command[4])))
        if command[2] == audio.SPEECH_SCRIPT:
            return subprocess.CompletedProcess(command, 1, "", "no speech detector in this test")
        shutil.copyfile(command[command.index("-i") + 1], command[command.index("-o") + 1])
        return subprocess.CompletedProcess(command, 0, json.dumps({"offset_seconds": 0.5, "framerate_scale_factor": 1.0, "sync_was_successful": True}), "")
    monkeypatch.setattr(audio, "run_tool", recorded)
    outcome, _ = audio.audio_fix(video, str(subtitle), parse_subtitle(str(subtitle)))
    assert outcome["decision"] == DECISION["audio_synced"] and [command[2] for command, _ in commands] == [audio.AUDIO_SYNC_SCRIPT, audio.SPEECH_SCRIPT]
    assert all(command[4].endswith(".flac") and size > 0 for command, size in commands)
    assert not any(str(library) in argument for command, _ in commands for argument in command)
    assert not os.listdir(audio.state_path("audio-work"))


def test_an_allowed_folder_reached_through_a_link_still_opens_its_videos(tmp_path, monkeypatch):
    real = tmp_path / "disk" / "media"
    real.mkdir(parents=True)
    (real / "Movie.mkv").write_bytes(b"video")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "media").symlink_to(real)
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [str(real.resolve())])
    monkeypatch.setattr(files, "SPELLING_OF", {str(real.resolve()): str(tmp_path / "data" / "media")})
    with files.opened_video(str(tmp_path / "data" / "media" / "Movie.mkv")) as descriptor:
        assert os.read(descriptor, 5) == b"video"
    (real / "inside").symlink_to(tmp_path / "disk")
    with pytest.raises(ProbeError), files.opened_video(str(tmp_path / "data" / "media" / "inside" / "media" / "Movie.mkv")):
        pass
