import pytest

from subtitle_guard import jobs, logs, storage


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STATE_DIRECTORY", str(tmp_path / "state"))
    monkeypatch.setattr(storage, "BACKUP_DIRECTORY", str(tmp_path / "backup"))
    monkeypatch.setattr(logs, "LOG_FILE", str(tmp_path / "state" / "subtitle-guard.log"))
    monkeypatch.setattr(jobs, "DATABASE", str(tmp_path / "state" / "jobs.sqlite3"))
    return tmp_path
