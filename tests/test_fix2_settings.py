# SPDX-License-Identifier: GPL-3.0-or-later
import pytest
from fastapi.testclient import TestClient

from oncue import configuration
from oncue.api import auth
from oncue.api.app import create_app
from oncue.api.security import API_KEY_HEADER, CSRF_HEADER
from oncue.errors import ConfigError

PASSWORD = "a long enough password"


def test_changing_the_login_mode_or_the_url_base_needs_the_password_when_a_login_is_needed(monkeypatch):
    monkeypatch.setenv("ONCUE_UI_PASSWORD", PASSWORD)
    client = TestClient(create_app("0.0.0.0"), base_url="http://guard.example")
    client.headers.update({CSRF_HEADER: client.post("/api/session", json={"user": "admin", "password": PASSWORD}).json()["csrf"]})
    refused = client.patch("/api/settings", json={"changes": {"auth_mode": "local"}})
    assert refused.status_code == 403 and refused.json()["code"] == "wrong_password"
    assert client.patch("/api/settings", json={"changes": {"url_base": "/guard"}, "password": "wrong"}).status_code == 403
    by_key = TestClient(client.app, base_url="http://guard.example", headers={API_KEY_HEADER: auth.api_key()})
    assert by_key.patch("/api/settings", json={"changes": {"url_base": "/guard"}}).status_code == 403
    assert by_key.patch("/api/settings", json={"changes": {"keep_days": 7}}).status_code == 200
    assert configuration.value("auth_mode") == "forms" and configuration.value("url_base") == ""
    assert client.patch("/api/settings", json={"changes": {"auth_mode": "local", "url_base": "/guard"}, "password": PASSWORD}).status_code == 200
    assert configuration.value("auth_mode") == "local" and configuration.value("url_base") == "/guard"


def test_without_a_login_the_url_base_changes_without_a_password():
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    client.headers.update({CSRF_HEADER: client.get("/api/session").json()["csrf"]})
    assert client.patch("/api/settings", json={"changes": {"url_base": "/oncue"}}).status_code == 200


@pytest.mark.parametrize("base", ["/a b", '/x"><script>', "/guard/", "guard", "/a//b", "/a/../b", "/é"])
def test_the_url_base_only_takes_plain_path_segments(base):
    with pytest.raises(ConfigError):
        configuration.validated(configuration.SETTINGS["url_base"], base)


@pytest.mark.parametrize("base", ["", "/oncue", "/apps/sub_guard.v2"])
def test_plain_url_bases_are_kept(base):
    assert configuration.validated(configuration.SETTINGS["url_base"], base) == base
