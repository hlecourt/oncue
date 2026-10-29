# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
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
    jobs.fail(job_id, 1, "boom")
    assert cli.main() == 0 and jobs.job(job_id)["status"] == "queued"


def test_hook_queue_flag_works_without_the_environment(monkeypatch):
    monkeypatch.delenv("SUBTITLE_GUARD_QUEUE", raising=False)
    monkeypatch.setattr(sys, "argv", ["subtitle-guard", "hook", "--queue", "/m.mkv", "/m.fr.srt", "fr", "opensubtitlescom", "9", "512"])
    assert cli.main() == 0
    assert jobs.recent()[0]["arguments"] == {"video": "/m.mkv", "subtitle": "/m.fr.srt", "language": "fr", "provider": "opensubtitlescom",
                                             "subtitle_id": "9", "media_id": "512", "content_hash": None}


def test_serve_installs_the_hook_package_where_bazarr_can_run_it(tmp_path):
    hook_folder = tmp_path / "hook"
    cli.install_hook_package(str(hook_folder))
    (hook_folder / "subtitle_guard" / "stale.py").write_text("old")
    cli.install_hook_package(str(hook_folder))
    assert not (hook_folder / "subtitle_guard" / "stale.py").exists() and sorted(os.listdir(hook_folder)) == ["subtitle_guard"]
    result = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import subtitle_guard.bazarr, subtitle_guard; print(subtitle_guard.__file__)",
                             str(hook_folder)], capture_output=True, text=True, check=True)
    assert result.stdout.startswith(str(hook_folder))


def test_an_unwritable_state_folder_is_a_clear_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    from subtitle_guard import storage
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(storage, "STATE_DIRECTORY", str(blocker / "state"))
    monkeypatch.setattr(sys, "argv", ["subtitle-guard", "check", "/x.mkv", "/x.srt"])
    assert cli.main() == 2
    message = capsys.readouterr().err
    assert "cannot be written" in message and "SUBTITLE_GUARD_STATE" in message
