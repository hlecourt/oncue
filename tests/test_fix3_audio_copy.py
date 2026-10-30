# SPDX-License-Identifier: GPL-3.0-or-later
from oncue import audio_copy, jobs


def test_a_failed_audio_copy_is_made_again_only_when_asked(tmp_path):
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"video")
    job, failed = audio_copy.request_copy(str(video), 1)
    assert job and not failed and audio_copy.request_copy(str(video), 1) == (job, False)
    jobs.claim(1)
    jobs.fail(job, 1, "ProbeError: ffmpeg could not make the audio copy")
    assert audio_copy.request_copy(str(video), 1) == (None, True) and audio_copy.request_copy(str(video), 1) == (None, True)
    assert len(jobs.recent()) == 1
    retried, failed = audio_copy.request_copy(str(video), 1, retry=True)
    assert retried not in (None, job) and not failed and audio_copy.request_copy(str(video), 1) == (retried, False)
    assert audio_copy.request_copy(str(video), 2)[1] is False
