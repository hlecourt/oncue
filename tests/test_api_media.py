# SPDX-License-Identifier: GPL-3.0-or-later
import shutil
import subprocess

import numpy
import pytest
from fastapi.testclient import TestClient
from media import video_with_reference, write_subtitle_file

from subtitle_guard import audio_copy, jobs, reports, worker
from subtitle_guard.api.app import create_app
from subtitle_guard.api.security import CSRF_HEADER

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


@pytest.fixture
def client():
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1")
    client.headers.update({CSRF_HEADER: client.get("/api/session").json()["csrf"]})
    return client


@pytest.fixture
def listened(tmp_path, client):
    video, starts, ends = video_with_reference(tmp_path, minutes=2, with_audio=True)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value)
    reports.report(str(subtitle), "scan", {"decision": "kept"})
    return client.get("/api/history").json()["rows"][0]["subtitle_id"], video


def run_waiting_jobs():
    while (job := jobs.claim(1)) is not None:
        jobs.finish(job["id"], 1, worker.run_job(job))


def test_the_audio_copy_is_made_once_by_a_job_then_streamed_with_ranges_and_peaks(client, listened):
    identity, _ = listened
    first = client.post(f"/api/subtitles/{identity}/audio", json={}).json()
    assert first["ready"] is False and first["job"] and first["track"] == 2 and first["tracks"][0]["codec"] == "aac"
    assert client.post(f"/api/subtitles/{identity}/audio", json={}).json()["job"] == first["job"]
    assert client.get(f"/api/subtitles/{identity}/audio/2").status_code == 404
    run_waiting_jobs()
    again = client.post(f"/api/subtitles/{identity}/audio", json={"track": 2}).json()
    assert again["ready"] is True and again["job"] is None
    part = client.get(f"/api/subtitles/{identity}/audio/2", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100 and part.headers["content-type"] == "audio/mp4"
    peaks = client.get(f"/api/subtitles/{identity}/audio/2/peaks").content
    assert abs(len(peaks) / again["peaks_per_second"] - again["duration"]) < 1.0 and max(peaks) > 20
    assert client.post(f"/api/subtitles/{identity}/audio", json={"track": 7}).json()["code"] == "unknown_track"


def test_a_still_frame_is_a_jpeg_and_times_are_bounded(client, listened):
    identity, _ = listened
    answer = client.get(f"/api/subtitles/{identity}/frame", params={"time": 12.5})
    assert answer.status_code == 200 and answer.content[:2] == b"\xff\xd8"
    assert client.get(f"/api/subtitles/{identity}/frame", params={"time": -1}).status_code == 422


def test_the_copy_keeps_the_subtitle_clock_when_the_audio_starts_late(tmp_path):
    plain, delayed = tmp_path / "plain.mkv", tmp_path / "delayed.mkv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=25:duration=30", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,10,10.1),sin(2*PI*1000*t),0)':s=48000:d=30", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(plain)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(plain), "-itsoffset", "1.0", "-i", str(plain), "-map", "0:v", "-map", "1:a",
                    "-c", "copy", str(delayed)], check=True)
    for video, expected in ((plain, 10.0), (delayed, 11.0)):
        audio_copy.make_copy(str(video), 1)
        peaks = numpy.fromfile(audio_copy.copy_paths(str(video), 1)[1], dtype=numpy.uint8)
        assert int(numpy.argmax(peaks)) / audio_copy.PEAKS_PER_SECOND == pytest.approx(expected, abs=0.06)
