# SPDX-License-Identifier: GPL-3.0-or-later
import sys

from subtitle_guard import cli, jobs


def test_options_do_not_swallow_positional_values():
    assert cli.parse_options(["2", "--apply", "--workers", "3", "--queue"]) == (["2"], {"--apply": True, "--queue": True, "--problems": False}, 3)


def test_hook_queues_a_job_in_queue_mode(monkeypatch, capsys):
    monkeypatch.setenv("SUBTITLE_GUARD_QUEUE", "1")
    monkeypatch.setattr(sys, "argv", ["subtitle-guard", "hook", "/v.mkv", "/v.fr.srt", "fr", "gestdown", "abc", "1693", ""])
    assert cli.main() == 0
    queued = jobs.recent()[0]
    assert queued["kind"] == "hook" and queued["arguments"]["media_id"] == "1693" and queued["arguments"]["series_id"] == ""
    assert "queued job" in capsys.readouterr().out


def test_retry_only_requeues_finished_jobs(monkeypatch, capsys):
    job_id = jobs.enqueue(jobs.KIND["check"], {"subtitle": "s.srt"})
    monkeypatch.setattr(sys, "argv", ["subtitle-guard", "retry", str(job_id)])
    assert cli.main() == 1
    jobs.claim(1)
    jobs.fail(job_id, "boom")
    assert cli.main() == 0 and jobs.job(job_id)["status"] == "queued"


def test_hook_queue_flag_works_without_the_environment(monkeypatch):
    monkeypatch.delenv("SUBTITLE_GUARD_QUEUE", raising=False)
    monkeypatch.setattr(sys, "argv", ["subtitle-guard", "hook", "--queue", "/m.mkv", "/m.fr.srt", "fr", "opensubtitlescom", "9", "512"])
    assert cli.main() == 0
    assert jobs.recent()[0]["arguments"] == {"video": "/m.mkv", "subtitle": "/m.fr.srt", "language": "fr", "provider": "opensubtitlescom",
                                             "subtitle_id": "9", "media_id": "512", "content_hash": None}
