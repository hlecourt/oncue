# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil

import pytest
from fastapi.testclient import TestClient
from media import video_with_reference, write_subtitle_file

from subtitle_guard import files, guard, jobs, reports, storage
from subtitle_guard.api.app import create_app
from subtitle_guard.api.security import CSRF_HEADER
from subtitle_guard.files import file_hash
from subtitle_guard.subtitles import parse_subtitle

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


@pytest.fixture
def client():
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    client.headers.update({CSRF_HEADER: client.get("/api/session").json()["csrf"]})
    return client


@pytest.fixture
def proposed(tmp_path, monkeypatch, client):
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    monkeypatch.setattr(files, "ROOTS", [os.path.realpath(tmp_path)])
    monkeypatch.setattr(guard, "confidence", lambda outcome: (65, "medium"))
    reports.report(str(subtitle), "scan", guard.evaluate_and_fix(str(video), str(subtitle), True))
    return client.get("/api/review").json()[0]["id"], subtitle, starts


def test_applying_the_proposal_needs_the_token_and_the_version_the_page_saw(client, proposed):
    identity, subtitle, starts = proposed
    version = file_hash(str(subtitle))
    assert client.post(f"/api/subtitles/{identity}/apply", json={"version": version}, headers={CSRF_HEADER: ""}).status_code == 403
    assert client.post(f"/api/subtitles/{identity}/apply", json={"version": "0" * 40}).json()["code"] == "stale_version"
    answer = client.post(f"/api/subtitles/{identity}/apply", json={"version": version})
    assert answer.status_code == 200 and answer.json()["decision"] == "approved"
    moved = [start for start, _, _ in parse_subtitle(str(subtitle))["cues"] if 119 <= start < 181]
    assert max(abs(a - b) for a, b in zip(moved, [start for start in starts if 120 <= start < 180], strict=False)) < 0.25
    assert client.get("/api/review").json() == []


def test_lines_move_and_any_saved_version_comes_back(client, proposed):
    identity, subtitle, _ = proposed
    before = subtitle.read_bytes()
    answer = client.post(f"/api/subtitles/{identity}/shift", json={"version": file_hash(str(subtitle)), "first": 0, "last": 9999, "offset": -0.5})
    assert answer.status_code == 200 and subtitle.read_bytes() != before
    assert client.post(f"/api/subtitles/{identity}/shift", json={"version": file_hash(str(subtitle)), "first": 0, "last": 9999, "offset": "nan"}).status_code == 422
    moved = subtitle.read_bytes()
    for refused in ({"first": 0, "last": 9999, "offset": -600}, {"first": 100, "last": 10, "offset": 1}):
        answer = client.post(f"/api/subtitles/{identity}/shift", json={"version": file_hash(str(subtitle)), **refused})
        assert answer.status_code == 422 and answer.json()["code"] == "refused"
    assert subtitle.read_bytes() == moved
    saved = next(item for item in client.get(f"/api/subtitles/{identity}").json()["versions"] if item["label"] == "before-manual")
    assert client.post(f"/api/subtitles/{identity}/restore", json={"version": file_hash(str(subtitle)), "saved": "../../etc/passwd"}).status_code == 422
    answer = client.post(f"/api/subtitles/{identity}/restore", json={"version": file_hash(str(subtitle)), "saved": saved["id"]})
    assert answer.status_code == 200 and subtitle.read_bytes() == before


def test_keep_check_again_ask_bazarr_and_delete(client, proposed):
    identity, subtitle, _ = proposed
    assert client.post(f"/api/subtitles/{identity}/keep").status_code == 200 and client.get("/api/review").json() == []
    job = client.post(f"/api/subtitles/{identity}/recheck").json()["job"]
    assert jobs.job(job)["arguments"]["subtitle"] == str(subtitle)
    assert client.post(f"/api/subtitles/{identity}/replacement").json()["code"] == "not_from_bazarr"
    assert client.post(f"/api/subtitles/{identity}/delete", json={"version": "1" * 40}).status_code == 409 and subtitle.exists()
    assert client.post(f"/api/subtitles/{identity}/delete", json={"version": file_hash(str(subtitle))}).status_code == 200
    assert not subtitle.exists() and [name for folder in storage.backup_folders(str(subtitle)) for name in os.listdir(folder)] == ["deleted.srt"]


def test_bulk_actions_report_what_was_done_and_what_was_skipped(client, proposed):
    identity, _, _ = proposed
    answer = client.post("/api/review/bulk", json={"action": "replacement", "ids": [identity, "unknown12345"]}).json()
    assert answer["done"] == [] and set(answer["skipped"]) == {identity, "unknown12345"}
    answer = client.post("/api/review/bulk", json={"action": "keep", "ids": [identity, identity]}).json()
    assert answer["done"] == [identity] and client.get("/api/review").json() == []
    assert client.post("/api/review/bulk", json={"action": "apply", "ids": [identity]}).status_code == 422


def test_only_a_finished_job_inside_the_allowed_folders_runs_again(client, proposed):
    _, subtitle, _ = proposed
    outside = jobs.enqueue(jobs.KIND["check"], {"video": "/v.mkv", "subtitle": str(subtitle), "apply": True})
    job = jobs.enqueue(jobs.KIND["check"], {"video": str(subtitle.parent / "Movie.2020.mkv"), "subtitle": str(subtitle), "apply": True})
    assert client.post(f"/api/jobs/{job}/retry").json()["code"] == "not_retryable"
    jobs.claim(1)
    jobs.finish(outside, 1, {"decision": "kept"})
    assert client.post(f"/api/jobs/{outside}/retry").json()["code"] == "not_retryable"
    jobs.claim(1)
    jobs.finish(job, 1, {"decision": "kept"})
    assert client.post(f"/api/jobs/{job}/retry").status_code == 202 and jobs.job(job)["status"] == "queued"


def test_without_allowed_folders_the_ui_changes_no_file(client, proposed, monkeypatch):
    identity, subtitle, _ = proposed
    monkeypatch.setattr(files, "ROOTS", [])
    before = subtitle.read_bytes()
    for action, body in (("apply", {"version": file_hash(str(subtitle))}), ("delete", {"version": file_hash(str(subtitle))}),
                         ("shift", {"version": file_hash(str(subtitle)), "first": 0, "last": 9999, "offset": 1})):
        answer = client.post(f"/api/subtitles/{identity}/{action}", json=body)
        assert answer.status_code == 409 and answer.json()["code"] == "no_allowed_folders"
    assert subtitle.read_bytes() == before
    assert client.get("/api/system").json()["checks"]["allowed_folders"]["ok"] is False
