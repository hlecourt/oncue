# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from subtitle_guard import heartbeats, logs, storage, worker


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


def test_a_second_supervisor_is_refused():
    first = worker.single_supervisor_lock()
    assert first is not None and worker.single_supervisor_lock() is None
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

    from subtitle_guard import tools
    from subtitle_guard.errors import ToolError
    marker = tmp_path / "child-alive"
    script = f"import subprocess, time; subprocess.Popen(['sh', '-c', 'sleep 2; touch {marker}']); time.sleep(30)"
    with pytest.raises(ToolError):
        tools.run_tool(["python3", "-c", script], 0.5)
    time.sleep(2.5)
    assert not marker.exists() and not tools.ACTIVE_GROUPS
    subprocess.run(["true"], check=True)


def test_a_crashed_role_is_restarted_and_a_stop_ends_every_role(tmp_path):
    marker = str(tmp_path / "role")
    script = ("import sys; from subtitle_guard import worker; import roles; worker.SUPERVISION_SECONDS = 0.2; "
              "sys.exit(worker.supervise({'worker-1': (roles.crash_once_then_wait, (sys.argv[1],))}))")
    environment = {**os.environ, "SUBTITLE_GUARD_STATE": str(tmp_path / "state"), "PYTHONPATH": os.path.dirname(__file__)}
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
    events = [json.loads(line)["event"] for line in (tmp_path / "state" / "subtitle-guard.log").read_text().splitlines()]
    assert "supervisor.role-restarted" in events and "supervisor.stopped" in events
