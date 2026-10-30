# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import subprocess

import pytest

from oncue import preview

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    folder = tmp_path_factory.mktemp("preview")
    plain = folder / "plain.mkv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=25:duration=30", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,10,10.1),sin(2*PI*1000*t),0)':s=48000:d=30", "-c:v", "mpeg4", "-c:a", "aac", "-metadata:s:a:0", "language=fre", "-disposition:a:0", "default",
                    "-shortest", str(plain)], check=True)
    return {"plain": str(plain)}


def test_tracks_duration_and_a_still_frame(videos):
    assert [(track["index"], track["language"], track["default"]) for track in preview.audio_tracks(videos["plain"])] == [(1, "fre", True)]
    assert preview.video_duration(videos["plain"]) == pytest.approx(30, abs=0.1)
    assert preview.still_frame(videos["plain"], 12.0)[:2] == b"\xff\xd8"
    assert not os.listdir(preview.state_path("preview-work"))
