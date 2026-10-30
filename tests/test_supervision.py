# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from oncue import heartbeats, jobs, logs, storage, worker


class Stop(Exception):
    pass


class Clock:
    def __init__(self, on_sleep):
        self.on_sleep, self.slept = on_sleep, []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.on_sleep(len(self.slept))

    def time(self):
        return time.time()


class FakeProcess:
    def __init__(self, pid):
        self.pid, self.exitcode, self.alive, self.stubborn, self.signals = pid, None, True, False, []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        if signal.SIGKILL in self.signals or (signal.SIGTERM in self.signals and not self.stubborn):
            self.alive = False


@pytest.fixture
def supervision(monkeypatch):
    started, handlers, by_pid = [], {}, {}

    def fake_start(target, arguments):
        process = FakeProcess(90000 + len(started))
        by_pid[process.pid] = process
        started.append((target, arguments, process))
        return process

    def killpg(pid, number):
        if pid not in by_pid:
            raise ProcessLookupError(pid)
        by_pid[pid].signals.append(number)
    monkeypatch.setattr(worker, "start", fake_start)
    monkeypatch.setattr(worker.os, "killpg", killpg)
    monkeypatch.setattr(worker.signal, "signal", handlers.__setitem__)
    monkeypatch.setitem(logs.settings, "echo", False)
    monkeypatch.setenv(heartbeats.SUPERVISED_VARIABLE, "")
    yield SimpleNamespace(started=started, handlers=handlers, by_pid=by_pid)
    os.close(worker.SUPERVISOR_LOCK.pop("descriptor"))


def logged_events():
    return [json.loads(line) for line in Path(storage.state_path("oncue.log")).read_text().splitlines()]


def serve_web():
    pass


def report_signal_handlers(path, descriptor):
    try:
        os.fstat(descriptor)
        lock_closed = False
    except OSError:
        lock_closed = True
    with open(path, "w") as handle:
        json.dump({"term": signal.getsignal(signal.SIGTERM) is worker.stop_role, "group": os.getpgid(0) == os.getpid(), "lock_closed": lock_closed}, handle)


def test_roles_get_their_own_handler_group_and_never_keep_the_supervisor_lock(tmp_path):
    descriptor = worker.single_supervisor_lock()
    signal.signal(signal.SIGTERM, lambda number, frame: None)
    try:
        process = worker.start(report_signal_handlers, (str(tmp_path / "result.json"), descriptor))
        process.join(timeout=20)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.close(descriptor)
        worker.SUPERVISOR_LOCK.clear()
    assert json.loads((tmp_path / "result.json").read_text()) == {"term": True, "group": True, "lock_closed": True}


def test_a_second_supervisor_is_refused(monkeypatch):
    monkeypatch.setitem(logs.settings, "echo", False)
    first = worker.single_supervisor_lock()
    assert first is not None and worker.single_supervisor_lock() is None and worker.supervise({}) == 1
    os.close(first)
    second = worker.single_supervisor_lock()
    assert second is not None
    os.close(second)
    worker.SUPERVISOR_LOCK.clear()


def test_worker_health_reads_fresh_heartbeats_of_a_live_supervisor():
    storage.write_json(storage.state_path("supervisor.json"), {"pid": os.getpid(), "workers": ["worker-1", "worker-2"]})
    os.makedirs(storage.state_path("heartbeats"), exist_ok=True)
    open(storage.state_path("heartbeats", "worker-1"), "w").close()
    stale = storage.state_path("heartbeats", "worker-2")
    open(stale, "w").close()
    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    assert heartbeats.worker_health() == {"worker-1": True, "worker-2": False}
    storage.write_json(storage.state_path("supervisor.json"), {"pid": 999999, "workers": ["worker-1"]})
    assert heartbeats.worker_health() == {}


def test_a_log_write_failure_never_raises(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(logs, "log_file", lambda: str(tmp_path))
    logs.log("job.started", "job 1 started")
    assert "could not write the log" in capsys.readouterr().err


def test_a_timed_out_tool_is_killed_with_its_children(tmp_path):
    import subprocess

    import pytest

    from oncue import tools
    from oncue.errors import ToolError
    marker = tmp_path / "child-alive"
    script = f"import subprocess, time; subprocess.Popen(['sh', '-c', 'sleep 2; touch {marker}']); time.sleep(30)"
    with pytest.raises(ToolError):
        tools.run_tool(["python3", "-c", script], 0.5)
    time.sleep(2.5)
    assert not marker.exists() and not tools.ACTIVE_GROUPS
    subprocess.run(["true"], check=True)


def test_a_crashed_role_is_restarted_and_a_stop_ends_every_role(tmp_path):
    marker = str(tmp_path / "role")
    script = ("import sys; from oncue import worker; import roles; worker.SUPERVISION_SECONDS = 0.2; "
              "sys.exit(worker.supervise({'worker-1': (roles.crash_once_then_wait, (sys.argv[1],))}))")
    environment = {**os.environ, "ONCUE_STATE": str(tmp_path / "state"), "PYTHONPATH": os.path.dirname(__file__)}
    supervisor = subprocess.Popen([sys.executable, "-c", script, marker], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while not os.path.exists(marker + ".restarted") and time.time() < deadline:
        time.sleep(0.1)
    assert os.path.exists(marker + ".restarted"), "the crashed role was not restarted"
    restarted = int(Path(marker + ".restarted").read_text())
    supervisor.send_signal(signal.SIGTERM)
    assert supervisor.wait(timeout=20) == 0
    time.sleep(0.2)
    try:
        os.kill(restarted, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive and not os.path.exists(tmp_path / "state" / "supervisor.json")
    events = [json.loads(line)["event"] for line in (tmp_path / "state" / "oncue.log").read_text().splitlines()]
    assert "supervisor.role-restarted" in events and "supervisor.stopped" in events


def test_the_supervisor_restarts_a_dead_worker_with_its_job_and_follows_the_settings(supervision, monkeypatch):
    first = worker.worker_roles(2)
    later = {"worker-1": first["worker-1"], "web": (serve_web, ())}
    wanted = iter([first, later, later])
    job_id = jobs.enqueue(jobs.KIND["check"], {"subtitle": "a.srt"})

    def on_sleep(count):
        if count == 1:
            jobs.claim(1)
            supervision.started[0][2].alive, supervision.started[0][2].exitcode = False, 3
            supervision.started[1][2].stubborn = True
        if count == 3:
            raise Stop

    def webhook_down():
        raise OSError("webhook down")
    clock = Clock(on_sleep)
    monkeypatch.setattr(worker, "time", clock)
    monkeypatch.setattr(worker, "send_due", webhook_down)
    with pytest.raises(Stop):
        worker.supervise(first, lambda: next(wanted))
    assert [(target, arguments) for target, arguments, _ in supervision.started] == [
        (worker.work_forever, (1,)), (worker.work_forever, (2,)), (worker.work_forever, (1,)), (serve_web, ())]
    assert supervision.started[1][2].signals == [signal.SIGTERM, signal.SIGKILL] and not supervision.started[2][2].signals
    assert jobs.job(job_id)["status"] == "queued" and clock.slept == [worker.SUPERVISION_SECONDS] * 3
    assert storage.read_json(storage.state_path("supervisor.json"))["workers"] == ["worker-1"]
    assert os.environ[heartbeats.SUPERVISED_VARIABLE] == "1"
    events = [event["event"] for event in logged_events()]
    assert {"supervisor.started", "supervisor.role-restarted", "webhook.error", "supervisor.roles-changed"} <= set(events)


def test_a_stop_signal_ends_every_role_and_kills_the_ones_that_stay(supervision, monkeypatch):
    def on_sleep(count):
        web = supervision.started[2][2]
        web.alive = False
        del supervision.by_pid[web.pid]
        supervision.started[1][2].stubborn = True
        supervision.handlers[signal.SIGTERM](signal.SIGTERM, None)
    monkeypatch.setattr(worker, "time", Clock(on_sleep))
    with pytest.raises(SystemExit) as stopped:
        worker.supervise({**worker.worker_roles(2), "web": (serve_web, ())})
    first, stubborn, web = (process for _, _, process in supervision.started)
    assert stopped.value.code == 0 and set(supervision.handlers) == {signal.SIGTERM, signal.SIGINT}
    assert first.signals == [signal.SIGTERM] and stubborn.signals == [signal.SIGTERM, signal.SIGKILL] and not web.signals
    assert not os.path.exists(storage.state_path("supervisor.json"))
    assert next(event for event in logged_events() if event["event"] == "supervisor.stopped")["killed"] == 1


def test_the_worker_count_comes_from_the_settings_unless_given(monkeypatch):
    monkeypatch.setenv("ONCUE_WORKERS", "3")
    monkeypatch.setattr(worker, "supervise", sorted)
    assert worker.run_workers() == ["worker-1", "worker-2", "worker-3"] and worker.run_workers(1) == ["worker-1"]


def test_an_idle_worker_polls_and_keeps_its_heartbeat_fresh(monkeypatch):
    monkeypatch.setitem(logs.settings, "echo", False)
    threads = []
    monkeypatch.setattr(worker, "threading", SimpleNamespace(Thread=lambda target, args, daemon: SimpleNamespace(start=lambda: threads.append((target, args)))))

    def on_sleep(count):
        if count >= 2:
            raise Stop
    clock = Clock(on_sleep)
    monkeypatch.setattr(worker, "time", clock)
    with pytest.raises(Stop):
        worker.work_forever(1)
    assert clock.slept == [worker.POLL_SECONDS] * 2 and threads == [(worker.beat, (1,))]
    with pytest.raises(Stop):
        worker.beat(1)
    assert Path(storage.state_path("heartbeats", "worker-1")).read_text() == str(os.getpid())
