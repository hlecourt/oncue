# SPDX-License-Identifier: GPL-3.0-or-later
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from subtitle_guard import bazarr, cli, logs
from subtitle_guard.guard import evaluate_and_fix


def events():
    return [json.loads(line) for line in open(logs.log_file())]


def test_events_are_json_lines_with_a_level_a_source_and_a_sentence():
    logs.log("job.done", "Finished task 3", job=3, decision="fixed", confidence=91)
    logs.log("job.failed", "Task 4 failed: boom", logs.LEVEL["error"], job=4)
    written = events()
    assert [event["event"] for event in written] == ["job.done", "job.failed"]
    assert written[0]["confidence"] == 91 and written[0]["level"] == "info" and written[0]["message"] == "Finished task 3" and "time" in written[0]
    assert written[1]["level"] == "error" and written[1]["source"] == "cli"


def test_log_rotates_past_its_size_limit(monkeypatch):
    monkeypatch.setattr(logs, "MAX_LOG_BYTES", 300)
    for number in range(20):
        logs.log("guard.checked", "Checked", number=number)
    assert len(Path(logs.log_file() + ".1").read_text()) > 0
    assert len(Path(logs.log_file()).read_text()) <= 600


def test_recent_events_are_newest_first_and_skip_a_cut_line(monkeypatch):
    for number in range(30):
        logs.log("job.queued", f"Queued task {number}", job=number)
    monkeypatch.setattr(logs, "TAIL_BYTES", 1500)
    recent = logs.recent_events()
    assert recent[0]["job"] == 29 and 3 < len(recent) < 30
    assert [event["job"] for event in recent] == sorted((event["job"] for event in recent), reverse=True)


def test_every_decision_is_logged_in_plain_words(tmp_path):
    subtitle = tmp_path / "x.fr.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
    evaluate_and_fix("/nonexistent.mkv", str(subtitle), False)
    event = events()[-1]
    assert event["event"] == "guard.checked" and event["decision"] == "unverified" and event["subtitle"] == str(subtitle)
    assert event["message"].startswith("decision=unverified confidence=0 band=none") and event["message"].endswith("dry-run")
    assert event["pid"] and event["source"] == "cli"


class Opener:
    def __init__(self, open_function):
        self.open = open_function


class Answer:
    status = 200

    def read(self, *size):
        return b'{"data": []}'

    def __enter__(self):
        return self

    def __exit__(self, *details):
        return False


def test_every_bazarr_request_is_logged_without_the_key(monkeypatch):
    monkeypatch.setenv("BAZARR_API_KEY", "secret-key")
    monkeypatch.setattr(bazarr, "OPENER", Opener(lambda request, timeout: Answer()))
    assert bazarr.bazarr_get("/api/movies?start=0&length=-1") == {"data": []}
    event = events()[-1]
    assert event["event"] == "bazarr.api-call" and event["status"] == 200 and event["path"] == "/api/movies?start=0&length=-1"
    assert event["message"].startswith("GET /api/movies?start=0&length=-1 200 in ")

    def refuse(request, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(bazarr, "OPENER", Opener(refuse))
    with pytest.raises(OSError):
        bazarr.bazarr_post("/api/episodes/blacklist", {"provider": "opensubtitles"})
    event = events()[-1]
    assert event["event"] == "bazarr.api-error" and event["level"] == "error" and event["method"] == "POST" and "connection refused" in event["error"]
    assert "secret-key" not in Path(logs.log_file()).read_text()


def test_a_download_from_bazarr_is_logged_as_it_arrives(monkeypatch, tmp_path):
    monkeypatch.setitem(logs.settings, "source", "cli")
    cli.hook([str(tmp_path / "a.mkv"), str(tmp_path / "a.fr.srt"), "fr", "opensubtitles", "123", "7", "2"], True)
    written = events()
    assert [event["event"] for event in written] == ["bazarr.hook-received", "job.queued"]
    assert written[0]["source"] == "hook" and written[0]["message"] == "post-processing hook lang=fr provider=opensubtitles id=123 mode=queued"
    assert written[1]["message"] == "job 1 queued kind=hook"


def test_a_long_running_command_reports_its_start_and_health(monkeypatch, tmp_path):
    from subtitle_guard import health
    monkeypatch.delenv("BAZARR_API_KEY", raising=False)
    monkeypatch.setattr(health, "BAZARR_CONFIG", str(tmp_path / "missing.yaml"))
    checks = health.report_startup("worker", workers=2)
    started, checked = events()[-2:]
    assert started["event"] == "service.started" and started["command"] == "worker" and started["workers"] == 2 and started["version"]
    assert checked["event"] == "health.checked" and checks["bazarr"] == {"ok": True, "configured": False} and checks["state_writable"]["ok"]
    assert checked["message"] == "health ok" or checked["message"].startswith("health degraded failing=")


def test_events_inside_a_job_carry_the_job_id(monkeypatch):
    from subtitle_guard import jobs, worker
    job_id = jobs.enqueue(jobs.KIND["check"], {"video": "/missing.mkv", "subtitle": "/missing.fr.srt", "apply": False})
    monkeypatch.setattr(worker, "run_job", lambda job: (logs.log("guard.checked", "decision=kept"), {"decision": "kept", "summary": "in sync"})[1])
    worker.work_once(1)
    by_event = {event["event"]: event for event in events()}
    assert by_event["guard.checked"]["job"] == job_id and by_event["job.succeeded"]["job"] == job_id
    logs.log("web.listening", "HTTP listening on 127.0.0.1:8484 auth=none")
    assert "job" not in events()[-1]
