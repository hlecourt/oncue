# SPDX-License-Identifier: GPL-3.0-or-later
import os
import time

import pytest

from oncue import audio, heartbeats, jobs, logs, storage, worker


class Stop(Exception):
    pass


class FakeProcess:
    def __init__(self, pid):
        self.pid, self.exitcode, self.alive = pid, None, True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass


@pytest.fixture
def supervision(monkeypatch):
    by_pid = {}

    def fake_start(target, arguments):
        process = FakeProcess(91000 + len(by_pid))
        by_pid[process.pid] = process
        return process

    def killpg(pid, number):
        by_pid[pid].alive = False
    monkeypatch.setattr(worker, "start", fake_start)
    monkeypatch.setattr(worker.os, "killpg", killpg)
    monkeypatch.setattr(worker.signal, "signal", lambda number, handler: None)
    monkeypatch.setattr(worker, "send_due", lambda: None)
    monkeypatch.setitem(logs.settings, "echo", False)
    monkeypatch.setenv(heartbeats.SUPERVISED_VARIABLE, "")
    yield monkeypatch
    os.close(worker.SUPERVISOR_LOCK.pop("descriptor"))


def run_supervisor(monkeypatch, roles, roles_for, on_sleep):
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        on_sleep(len(sleeps))
    monkeypatch.setattr(worker, "time", type("Clock", (), {"sleep": staticmethod(sleep), "time": staticmethod(time.time)}))
    with pytest.raises(Stop):
        worker.supervise(roles, roles_for)


def test_a_worker_removed_by_the_settings_gives_back_its_running_job_and_leaves_the_ready_list(supervision):
    job_id = jobs.enqueue(jobs.KIND["check"], {"subtitle": "a.srt"})
    wanted = iter([worker.worker_roles(1), worker.worker_roles(1)])

    def on_sleep(count):
        if count == 1:
            assert jobs.claim(2)["id"] == job_id
        if count == 2:
            raise Stop
    run_supervisor(supervision, worker.worker_roles(2), lambda: next(wanted), on_sleep)
    assert jobs.job(job_id)["status"] == "queued"
    assert storage.read_json(storage.state_path("supervisor.json"))["workers"] == ["worker-1"]


def test_the_supervisor_empties_the_audio_work_folder_left_by_killed_workers(supervision):
    work = storage.state_path(audio.AUDIO_WORK_FOLDER)
    os.makedirs(work)
    open(os.path.join(work, "soundtrack-left.flac"), "w").close()

    def on_sleep(count):
        raise Stop
    run_supervisor(supervision, worker.worker_roles(1), None, on_sleep)
    assert not os.path.exists(work)


def test_audio_temporary_files_are_removed_when_one_cannot_be_created(tmp_path, monkeypatch):
    real_mkstemp, calls = audio.tempfile.mkstemp, []

    def failing_third(**options):
        calls.append(options)
        if len(calls) == 3:
            raise OSError("disk full")
        return real_mkstemp(**options)
    monkeypatch.setattr(audio.tempfile, "mkstemp", failing_third)
    subtitle = tmp_path / "Movie.fr.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    with pytest.raises(OSError, match="disk full"):
        audio.audio_fix(str(tmp_path / "Movie.mkv"), str(subtitle), {"cues": []})
    assert os.listdir(storage.state_path(audio.AUDIO_WORK_FOLDER)) == []
