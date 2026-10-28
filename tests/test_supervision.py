# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import signal
import time

from subtitle_guard import heartbeats, logs, storage, worker


def report_signal_handlers(path):
    with open(path, "w") as handle:
        json.dump({"term": signal.getsignal(signal.SIGTERM) == signal.SIG_DFL, "group": os.getpgid(0) == os.getpid()}, handle)


def test_roles_start_with_default_signal_handlers_in_their_own_group(tmp_path):
    signal.signal(signal.SIGTERM, lambda number, frame: None)
    try:
        process = worker.start(report_signal_handlers, (str(tmp_path / "result.json"),))
        process.join(timeout=20)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    assert json.loads((tmp_path / "result.json").read_text()) == {"term": True, "group": True}


def test_a_second_supervisor_is_refused():
    first = worker.single_supervisor_lock()
    assert first is not None and worker.single_supervisor_lock() is None
    os.close(first)
    assert worker.single_supervisor_lock() is not None


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
