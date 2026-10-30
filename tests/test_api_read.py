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
    return TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


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
    assert item["reason"] == "A fix from the video's subtitles is ready" and item["tone"] == "warn"


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
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [str(tmp_path / "elsewhere")])
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
    reports.report(str(subtitle), "scan", {"decision": "fixed", "confidence": 100, "confidence_band": "high"}, applied=False)
    dry_run = client.get("/api/history", params={"result": "proposals"}).json()
    groups = {group["key"]: group["count"] for group in dry_run["groups"]}
    assert dry_run["rows"][0]["words"] == "Sure fix found, not written (report mode)" and groups["fixed"] == 0 and groups["proposals"] == 2


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


def test_a_dry_run_never_reads_as_if_the_file_was_written():
    from subtitle_guard.api.presentation import result_words
    row = {"decision": "fixed", "applied": 0, "note": "", "band": "high", "weak_minutes": ""}
    assert result_words(row)[0] == "Sure fix found, not written (report mode)"
    assert result_words({**row, "decision": "kept"})[0] == "In sync"
    assert result_words({**row, "applied": 1})[0] == "Fixed automatically"


def test_the_other_subtitles_of_the_same_video_are_listed(client, proposed):
    _, subtitle, _ = proposed
    sibling = subtitle.with_name("Movie.2020.en.srt")
    sibling.write_text(subtitle.read_text())
    reports.report(str(sibling), "scan", {"decision": "kept", "confidence": 95, "confidence_band": "high"})
    subtitle.with_name("Movie.2020.es.srt").write_text(subtitle.read_text())
    identity = client.get("/api/review").json()[0]["id"]
    siblings = client.get(f"/api/subtitles/{identity}").json()["siblings"]
    assert [(item["name"], item["words"]) for item in siblings] == [("Movie.2020.en.srt", "In sync")]
    assert client.get(f"/api/subtitles/{siblings[0]['id']}").json()["siblings"][0]["name"] == subtitle.name


def test_every_known_subtitle_is_listed_and_searchable_and_its_own_history_can_be_read(client, proposed):
    _, subtitle, _ = proposed
    identity = client.get("/api/review").json()[0]["id"]
    other = subtitle.with_name("Other.2021.fr.srt")
    other.write_text(subtitle.read_text())
    reports.report(str(other), "scan", {"decision": "kept", "confidence": 95, "confidence_band": "high"})
    listing = client.get("/api/subtitles").json()
    assert listing["total"] == 2 and [row["name"] for row in listing["rows"]] == ["Movie.2020.fr.srt", "Other.2021.fr.srt"]
    found = client.get("/api/subtitles", params={"q": "other fr"}).json()
    assert found["total"] == 1 and found["rows"][0]["words"] == "In sync"
    assert client.get("/api/subtitles", params={"limit": 1, "offset": 1}).json()["rows"][0]["name"] == "Other.2021.fr.srt"
    answer = client.get("/api/history", params={"subtitle": identity})
    assert answer.status_code == 200, answer.text
    own = answer.json()["rows"]
    assert own and all(row["name"] == "Movie.2020.fr.srt" for row in own)
    assert client.get("/api/history", params={"subtitle": "deadbeef0000"}).status_code == 404


def test_the_words_follow_the_language_of_the_request_and_fall_back_to_english(client, proposed):
    _, subtitle, _ = proposed
    french = client.get("/api/review", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).json()[0]
    assert french["reason"] == "Un correctif d'après les sous-titres de la vidéo est prêt"
    assert client.get("/api/review").json()[0]["reason"] == "A fix from the video's subtitles is ready"
    reports.report(str(subtitle), "scan", {"decision": "kept", "confidence": 90, "confidence_band": "high"})
    chinese = client.get("/api/history", params={"limit": 1}, headers={"Accept-Language": "zh-CN"}).json()
    assert chinese["rows"][0]["words"] == "已同步" and chinese["rows"][0]["started_by"] == "媒体库扫描"
    assert {group["key"]: group["label"] for group in chinese["groups"]}["proposals"] == "修正建议"
    german = client.get("/api/history", params={"limit": 1}, headers={"Accept-Language": "de-DE"}).json()
    assert german["rows"][0]["words"] == "In sync" and german["rows"][0]["started_by"] == "Library scan"
