# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import subprocess

import numpy
import pytest

from subtitle_guard import preview

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    folder = tmp_path_factory.mktemp("preview")
    plain, delayed = folder / "plain.mkv", folder / "delayed.mkv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=25:duration=30", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,10,10.1),sin(2*PI*1000*t),0)':s=48000:d=30", "-c:v", "mpeg4", "-c:a", "aac", "-metadata:s:a:0", "language=fre", "-disposition:a:0", "default",
                    "-shortest", str(plain)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(plain), "-itsoffset", "1.0", "-i", str(plain), "-map", "0:v", "-map", "1:a",
                    "-c", "copy", str(delayed)], check=True)
    return {"plain": str(plain), "delayed": str(delayed)}


def beep_at(wav):
    samples = numpy.frombuffer(wav[44:], dtype=numpy.int16)
    return int(numpy.argmax(numpy.abs(samples))) / preview.CLIP_SAMPLE_RATE


@pytest.mark.parametrize(("name", "start", "expected"), [("plain", 8, 2.0), ("delayed", 8, 3.0), ("delayed", 0, 11.0), ("delayed", 0.5, 10.5)])
def test_clip_time_plus_its_start_is_the_subtitle_clock_even_with_delayed_audio(videos, name, start, expected):
    wav = preview.audio_clip(videos[name], start, 1)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert beep_at(wav) == pytest.approx(expected, abs=0.03)


def test_tracks_duration_and_a_still_frame(videos):
    assert [(track["index"], track["language"], track["default"]) for track in preview.audio_tracks(videos["plain"])] == [(1, "fre", True)]
    assert preview.video_duration(videos["plain"]) == pytest.approx(30, abs=0.1)
    assert preview.still_frame(videos["plain"], 12.0)[:2] == b"\xff\xd8"
    assert not os.listdir(preview.state_path("preview-work"))
