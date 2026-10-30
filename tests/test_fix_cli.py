# SPDX-License-Identifier: GPL-3.0-or-later
import sys
from types import SimpleNamespace

import pytest

from subtitle_guard import cli, jobs

HOOK = ["/v.mkv", "/v.fr.srt", "fr", "subdl", "41", "7"]
DISPATCHED = [
    (["hook", *HOOK], "hook", (HOOK, False)),
    (["scan-series", "12", "--apply"], "scan_series", (12, True, False)),
    (["scan-movies", "--queue"], "scan_movies", (False, True)),
    (["scan-folder", "/media"], "scan_folder", ("/media", False, False)),
    (["watch", "/a", "/b", "--apply"], "watch", (["/a", "/b"], True)),
    (["worker", "--workers", "3"], "run_workers", (3,)),
    (["calibrate-audio", "/media", "5"], "calibrate_audio", ("/media", 5)),
    (["calibrate-audio", "/media", "many"], "calibrate_audio", ("/media", None)),
    (["needs-human"], "print_needs_human", ()),
    (["set-password", "alice"], "set_password", ("alice",)),
    (["set-password"], "set_password", ("admin",)),
    (["log", "5", "--problems"], "print_log", (5, True)),
    (["log"], "print_log", (50, False)),
    (["try-another", "/v.mkv", "/v.fr.srt"], "try_another_subtitle", ("/v.mkv", "/v.fr.srt")),
]


def run(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["subtitle-guard", *arguments])
    return cli.main()


@pytest.fixture(autouse=True)
def quiet_start(monkeypatch):
    monkeypatch.delenv("SUBTITLE_GUARD_QUEUE", raising=False)
    monkeypatch.delenv("SUBTITLE_GUARD_HOOK_DIR", raising=False)
    monkeypatch.setattr(cli, "report_startup", lambda *arguments, **details: None)


@pytest.mark.parametrize(("arguments", "target", "expected"), DISPATCHED)
def test_each_command_reaches_its_function_with_its_arguments(monkeypatch, arguments, target, expected):
    calls = []
    monkeypatch.setattr(cli, target, lambda *values: calls.append(values) or 0)
    assert run(monkeypatch, *arguments) == 0 and calls == [expected]


def test_check_and_enqueue_follow_the_apply_flag(monkeypatch, capsys):
    reported = []
    monkeypatch.setattr(cli, "evaluate_and_fix", lambda video, subtitle, apply: {"decision": "kept", "reason": f"apply={apply}"})
    monkeypatch.setattr(cli, "report", lambda subtitle, provider, outcome, applied: reported.append((subtitle, provider, applied)))
    assert run(monkeypatch, "check", "/v.mkv", "/v.fr.srt", "--apply") == 0 and reported == [("/v.fr.srt", "check", True)]
    assert "apply=True" in capsys.readouterr().out
    assert run(monkeypatch, "enqueue", "/v.mkv", "/v.fr.srt") == 0
    assert jobs.recent()[0]["arguments"] == {"video": "/v.mkv", "subtitle": "/v.fr.srt", "apply": False}


def test_scan_all_scans_every_series_then_the_movies(monkeypatch):
    scanned = []
    monkeypatch.setattr(cli, "bazarr_get", lambda path: {"data": [{"sonarrSeriesId": 3}, {"sonarrSeriesId": 8}]})
    monkeypatch.setattr(cli, "scan_series", lambda series, apply, queue: scanned.append(series))
    monkeypatch.setattr(cli, "scan_movies", lambda apply, queue: scanned.append("movies") or 0)
    assert run(monkeypatch, "scan-all", "--queue") == 0 and scanned == [3, 8, "movies"]


def test_ui_and_serve_start_the_web_app(monkeypatch):
    served, supervised = [], []
    monkeypatch.setattr(cli, "web_module", lambda: SimpleNamespace(HOST="127.0.0.1", PORT=8485, serve_api=lambda: served.append(True)))
    monkeypatch.setattr(cli, "supervise", lambda roles, roles_for: supervised.append(sorted(roles)) or 0)
    assert run(monkeypatch, "ui") == 0 and served == [True]
    assert run(monkeypatch, "serve", "--workers", "1") == 0 and supervised == [["web", "worker-1"]]
    monkeypatch.setattr(cli, "web_module", lambda: None)
    assert run(monkeypatch, "ui") == 2 and run(monkeypatch, "serve") == 2


def test_status_jobs_and_job_print_the_queue(monkeypatch, capsys):
    job_id = jobs.enqueue(jobs.KIND["check"], {"video": "/v.mkv", "subtitle": "/v.fr.srt", "apply": False})
    assert run(monkeypatch, "status") == 0 and run(monkeypatch, "jobs", "queued") == 0
    assert run(monkeypatch, "job", str(job_id)) == 0 and run(monkeypatch, "job", "999") == 1
    output = capsys.readouterr().out
    assert "v.fr.srt" in output and "no such job" in output


@pytest.mark.parametrize("arguments", [["job", "x"], ["retry", "4.5"], ["scan-series", "abc"], ["log", "ten"], ["worker", "--workers", "two"]])
def test_a_bad_number_is_a_usage_error_not_a_traceback(monkeypatch, capsys, arguments):
    assert run(monkeypatch, *arguments) == 2
    assert "is not a whole number" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [[], ["check", "/v.mkv"], ["try-another", "/v.mkv"], ["scan-folder"], ["watch"], ["unknown"]])
def test_a_wrong_command_or_count_prints_the_usage(monkeypatch, capsys, arguments):
    assert run(monkeypatch, *arguments) == 2 and "Commands:" in capsys.readouterr().out
