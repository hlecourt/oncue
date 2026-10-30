# SPDX-License-Identifier: GPL-3.0-or-later
import os

from oncue.api.routes import media
from oncue.storage import file_signature


def test_a_video_is_probed_once_until_it_changes(tmp_path, monkeypatch):
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"video")
    probed = []
    monkeypatch.setattr(media, "audio_tracks", lambda path: probed.append(path) or [{"index": 1}])
    monkeypatch.setattr(media, "video_duration", lambda path: 60.0)
    media.video_facts.cache_clear()
    assert media.video_facts(str(video), file_signature(str(video))) == ([{"index": 1}], 60.0)
    media.video_facts(str(video), file_signature(str(video)))
    assert len(probed) == 1
    os.utime(video, (1, 1))
    media.video_facts(str(video), file_signature(str(video)))
    assert len(probed) == 2
