import json

from subtitle_guard import logs
from subtitle_guard.guard import evaluate_and_fix


def test_events_are_json_lines(tmp_path):
    logs.log("job-done", job=3, decision="fixed", confidence=91)
    logs.log("job-failed", job=4, error="boom")
    events = [json.loads(line) for line in open(logs.LOG_FILE)]
    assert [event["event"] for event in events] == ["job-done", "job-failed"]
    assert events[0]["confidence"] == 91 and "time" in events[0]


def test_log_rotates_past_its_size_limit(monkeypatch):
    monkeypatch.setattr(logs, "MAX_LOG_BYTES", 200)
    for number in range(20):
        logs.log("decision", number=number)
    assert len(open(logs.LOG_FILE + ".1").read()) > 0
    assert len(open(logs.LOG_FILE).read()) <= 400


def test_every_decision_is_logged(tmp_path):
    subtitle = tmp_path / "x.fr.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
    evaluate_and_fix("/nonexistent.mkv", str(subtitle), False)
    event = json.loads(open(logs.LOG_FILE).read().splitlines()[-1])
    assert event["event"] == "decision" and event["decision"] == "unverified" and event["subtitle"] == str(subtitle)
