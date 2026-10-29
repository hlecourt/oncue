# SPDX-License-Identifier: GPL-3.0-or-later
import shutil

import pytest
from fastapi.testclient import TestClient
from media import video_with_reference, write_subtitle_file

from subtitle_guard import files, guard, jobs, logs, reports
from subtitle_guard.api.app import create_app

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


@pytest.fixture
def client():
    return TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1")


@pytest.fixture
def proposed(tmp_path, monkeypatch):
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    monkeypatch.setattr(guard, "confidence", lambda outcome: (65, "medium"))
    reports.report(str(subtitle), "opensubtitles", guard.evaluate_and_fix(str(video), str(subtitle), True))
    return video, subtitle, starts


def test_review_lists_the_proposal_with_an_opaque_id_and_plain_words(client, proposed):
    _, subtitle, _ = proposed
    items = client.get("/api/review").json()
    assert len(items) == 1 and str(subtitle) not in str(items)
    item = items[0]
    assert item["name"] == subtitle.name and item["folder"] == "library / Movie (2020)" and item["decision"] == "proposed"
    assert item["proposal"]["kind"] == "aligned" and item["proposal"]["confidence"] == 65 and " s earlier" in item["proposal"]["changes"][0]
    assert item["reason"] == "A fix from the video's subtitles is ready, confidence 65" and item["tone"] == "warn"


def test_a_subtitle_has_its_detail_its_lines_and_the_proposed_timings(client, proposed):
    _, subtitle, starts = proposed
    identity = client.get("/api/review").json()[0]["id"]
    detail = client.get(f"/api/subtitles/{identity}").json()
    assert detail["name"] == subtitle.name and detail["video"] == "Movie.2020.mkv" and detail["decision"] == "proposed" and detail["words"] == "Proposal to check"
    assert len(detail["parts"]) == 1 and detail["parts"][0]["shift"] == pytest.approx(-3, abs=0.15) and len(detail["version"]) == 40
    lines = client.get(f"/api/subtitles/{identity}/lines").json()
    assert len(lines["lines"]) == len(starts) and len(lines["reference"]) == len(starts) and len(lines["proposal"]) == len(starts)
    assert lines["version"] == detail["version"]


def test_unknown_ids_and_subtitles_outside_the_allowed_folders_are_not_found(client, proposed, tmp_path, monkeypatch):
    identity = client.get("/api/review").json()[0]["id"]
    assert client.get("/api/subtitles/deadbeef0000").status_code == 404
    monkeypatch.setattr(files, "ROOTS", [str(tmp_path / "elsewhere")])
    answer = client.get(f"/api/subtitles/{identity}")
    assert answer.status_code == 404 and answer.json()["code"] == "unknown_subtitle"


def test_history_pages_newest_first_with_counts_per_result(client, proposed):
    _, subtitle, _ = proposed
    reports.report(str(subtitle), "scan", {"decision": "kept", "confidence": 90, "confidence_band": "high"})
    first = client.get("/api/history", params={"limit": 1}).json()
    assert first["rows"][0]["decision"] == "kept" and first["rows"][0]["started_by"] == "Library scan" and first["next_before"]
    second = client.get("/api/history", params={"limit": 1, "before": first["next_before"]}).json()
    assert second["rows"][0]["decision"] == "proposed" and second["rows"][0]["started_by"] == "Bazarr (downloaded from opensubtitles)"
    groups = {group["key"]: group["count"] for group in first["groups"]}
    assert groups["proposals"] == 1 and groups["in-sync"] == 1
    assert [row["decision"] for row in client.get("/api/history", params={"result": "proposals"}).json()["rows"]] == ["proposed"]


def test_jobs_and_the_log_are_readable_and_searchable(client, proposed):
    _, subtitle, _ = proposed
    jobs.enqueue(jobs.KIND["check"], {"video": "/v.mkv", "subtitle": str(subtitle), "apply": True})
    assert client.get("/api/jobs").json()[0]["subtitle"] == subtitle.name
    logs.log("guard.checked", "a needle in the log", subtitle=str(subtitle))
    found = client.get("/api/log", params={"q": "needle"}).json()
    assert len(found) == 1 and found[0]["event"] == "guard.checked" and found[0]["details"]["subtitle"] == str(subtitle)
    assert client.get("/api/log", params={"limit": 5000}).status_code == 422


def test_a_subtitle_that_became_a_symlink_is_never_served(client, proposed, tmp_path):
    _, subtitle, _ = proposed
    identity = client.get("/api/review").json()[0]["id"]
    outside = tmp_path / "outside.srt"
    outside.write_text(subtitle.read_text())
    subtitle.unlink()
    subtitle.symlink_to(outside)
    assert client.get(f"/api/subtitles/{identity}").status_code == 404 and client.get(f"/api/subtitles/{identity}/lines").status_code == 404
