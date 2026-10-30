# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from oncue import files, logs, storage


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setattr(storage, "STATE_DIRECTORY", str(tmp_path / "state"))
    monkeypatch.setattr(storage, "BACKUP_DIRECTORY", str(tmp_path / "backup"))
    monkeypatch.setitem(logs.settings, "source", "cli")
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [os.path.realpath(tmp_path_factory.getbasetemp())])
    monkeypatch.delenv("ONCUE_UI_PASSWORD", raising=False)
    from oncue.api import auth
    auth.SESSIONS.clear()
    auth.FAILURES.clear()
    auth.ACCOUNT_FAILURES.clear()
    auth.KNOWN_CLIENTS.clear()
    auth.ACCOUNT.clear()
    return tmp_path
