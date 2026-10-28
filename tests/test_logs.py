import json
import urllib.error
import urllib.request

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
    assert len(open(logs.log_file() + ".1").read()) > 0
    assert len(open(logs.log_file()).read()) <= 600


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
    assert event["message"].startswith("Checked: Cannot be checked") and "dry run" in event["message"]


class Answer:
    status = 200

    def read(self):
        return b'{"data": []}'


def test_every_bazarr_request_is_logged_without_the_key(monkeypatch):
    monkeypatch.setenv("BAZARR_API_KEY", "secret-key")
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Answer())
    assert bazarr.bazarr_get("/api/movies?start=0&length=-1") == {"data": []}
    event = events()[-1]
    assert event["event"] == "bazarr.request" and event["status"] == 200 and event["path"] == "/api/movies?start=0&length=-1"

    def refuse(request, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(OSError):
        bazarr.bazarr_post("/api/episodes/blacklist", {"provider": "opensubtitles"})
    event = events()[-1]
    assert event["event"] == "bazarr.request-failed" and event["level"] == "error" and event["method"] == "POST"
    assert "secret-key" not in open(logs.log_file()).read()


def test_a_download_from_bazarr_is_logged_as_it_arrives(monkeypatch, tmp_path):
    monkeypatch.setitem(logs.settings, "source", "cli")
    cli.hook([str(tmp_path / "a.mkv"), str(tmp_path / "a.fr.srt"), "fr", "opensubtitles", "123", "7", "2"], True)
    written = events()
    assert [event["event"] for event in written] == ["bazarr.hook", "job.queued"]
    assert written[0]["source"] == "hook" and written[0]["message"] == "Bazarr downloaded a fr subtitle from opensubtitles, queued for a check"
