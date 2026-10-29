# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request

import pytest
from media import video_with_reference, write_subtitle_file
from test_web import get, notice_text, post, server  # noqa: F401

from subtitle_guard import reports, review
from subtitle_guard.bazarr import file_hash
from subtitle_guard.subtitles import parse_subtitle
from subtitle_guard.web import editor
from subtitle_guard.web import server as web_server

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


@pytest.fixture
def shifted(tmp_path):
    video, starts, ends = video_with_reference(tmp_path, minutes=3, with_audio=True)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 2)
    reports.report(str(subtitle), "scan", {"decision": "kept"})
    return video, subtitle, starts


def post_for_bytes(url, form, csrf="test-csrf"):
    data = urllib.parse.urlencode({**form, "csrf": csrf} if csrf else form).encode()
    request = urllib.request.Request(url, data=data, headers={"Cookie": f"{web_server.CSRF_COOKIE}=test-csrf"}, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read(), response.headers.get("Content-Type")
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers.get("Content-Type")


def test_the_editor_page_carries_the_lines_the_video_track_and_the_audio_tracks(server, shifted):  # noqa: F811
    _, subtitle, starts = shifted
    status, page, headers = get(server + "/edit?" + urllib.parse.urlencode({"path": str(subtitle), "at": "30"}))
    assert status == 200 and "media-src blob:" in headers["Content-Security-Policy"]
    data = json.loads(page.split('<script type="application/json" id="editor-data">')[1].split("</script>")[0])
    assert data["hash"] == file_hash(str(subtitle)) and data["at"] == 30 and data["duration"] > 100
    assert data["cues"][0][0] == pytest.approx(starts[0] + 2, abs=0.01) and data["reference"][0][0] == pytest.approx(starts[0], abs=0.01)
    assert [track["index"] for track in data["tracks"]] == [2] and "Apply to the file" in page
    assert "Listen and edit" in get(server + "/subtitle?" + urllib.parse.urlencode({"path": str(subtitle)}))[1]


def test_audio_and_frames_need_the_form_token_a_known_subtitle_and_a_free_slot(server, shifted):  # noqa: F811
    _, subtitle, _ = shifted
    status, body, content_type = post_for_bytes(server + "/api/audio", {"path": str(subtitle), "start": "60", "track": "2"})
    assert status == 200 and content_type == "audio/wav" and body[:4] == b"RIFF" and len(body) > 60 * 16000 * 2
    status, body, content_type = post_for_bytes(server + "/api/frame", {"path": str(subtitle), "time": "12.5"})
    assert status == 200 and content_type == "image/jpeg" and body[:2] == b"\xff\xd8"
    assert post_for_bytes(server + "/api/audio", {"path": str(subtitle), "start": "0", "track": "2"}, csrf=None)[0] == 403
    assert post_for_bytes(server + "/api/audio", {"path": "/etc/passwd", "start": "0", "track": "2"})[0] == 404
    assert post_for_bytes(server + "/api/audio", {"path": str(subtitle), "start": "-5", "track": "2"})[0] == 400
    assert post_for_bytes(server + "/api/audio", {"path": str(subtitle), "start": "0", "track": "0:v"})[0] == 400
    with editor.PREVIEW_SLOT:
        assert post_for_bytes(server + "/api/audio", {"path": str(subtitle), "start": "0", "track": "2"})[0] == 503


def test_applying_from_the_editor_moves_every_line_and_comes_back_to_the_editor(server, shifted):  # noqa: F811
    _, subtitle, starts = shifted
    form = {"action": "shift", "path": str(subtitle), "first": "0", "last": "9999", "offset": "-2", "hash": file_hash(str(subtitle)), "back": "edit", "at": "42.0"}
    code, location = post(server + "/", form)
    assert code == 303 and location.startswith("/edit?") and "at=42.0" in location and "now appear 2.0 s earlier" in notice_text(location)
    moved = [start for start, _, _ in parse_subtitle(str(subtitle))["cues"]]
    assert max(abs(a - b) for a, b in zip(moved, starts, strict=False)) < 0.01 and review.has_backup(str(subtitle))


def test_a_stale_page_a_negative_time_or_a_reversed_part_changes_nothing(server, shifted):  # noqa: F811
    _, subtitle, _ = shifted
    before = subtitle.read_bytes()
    base = {"action": "shift", "path": str(subtitle), "back": "edit"}
    stale = post(server + "/", {**base, "first": "0", "last": "9999", "offset": "1", "hash": "0" * 40})[1]
    assert "changed since this page was opened" in notice_text(stale)
    negative = post(server + "/", {**base, "first": "0", "last": "9999", "offset": "-600", "hash": file_hash(str(subtitle))})[1]
    assert "start before the beginning" in notice_text(negative)
    reversed_part = post(server + "/", {**base, "first": "100", "last": "10", "offset": "1"})[1]
    assert "ends before it starts" in notice_text(reversed_part)
    assert subtitle.read_bytes() == before and not review.has_backup(str(subtitle))


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
    monkeypatch.setattr(files, "ROOTS", [str(outside.resolve())])
    assert review.video_for(str(library / "Movie.fr.srt")) is None
    assert os.path.exists(library / "Movie.mkv")
