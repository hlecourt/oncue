# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from subtitle_guard import logs, storage


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STATE_DIRECTORY", str(tmp_path / "state"))
    monkeypatch.setattr(storage, "BACKUP_DIRECTORY", str(tmp_path / "backup"))
    monkeypatch.setitem(logs.settings, "source", "cli")
    monkeypatch.delenv("SUBTITLE_GUARD_UI_PASSWORD", raising=False)
    from subtitle_guard.api import auth
    auth.SESSIONS.clear()
    auth.FAILURES.clear()
    auth.ACCOUNT.clear()
    return tmp_path
