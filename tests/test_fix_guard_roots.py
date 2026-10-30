# SPDX-License-Identifier: GPL-3.0-or-later
import shutil

import pytest
from media import video_with_reference, write_subtitle_file

from subtitle_guard import files, guard, storage

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


def test_a_sure_fix_is_not_written_and_not_backed_up_without_allowed_folders(tmp_path, monkeypatch):
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3)
    before = subtitle.read_bytes()
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [])
    outcome = guard.evaluate_and_fix(str(video), str(subtitle), True)
    assert outcome["decision"] == "unverified" and "SUBTITLE_GUARD_ROOTS" in outcome["reason"]
    assert subtitle.read_bytes() == before
    assert not list((tmp_path / "backup").rglob("*.srt")) and str(tmp_path / "backup") == storage.BACKUP_DIRECTORY
