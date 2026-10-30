# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os

import pytest
from fastapi.testclient import TestClient

from oncue import configuration, files, storage
from oncue.api import auth
from oncue.api.app import create_app
from oncue.api.security import API_KEY_HEADER, CSRF_HEADER
from oncue.errors import ConfigError

PASSWORD = "a long enough password"


def local_client():
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    client.headers.update({CSRF_HEADER: client.get("/api/session").json()["csrf"]})
    return client


def setting(view, name):
    return next(item for item in view["settings"] if item["name"] == name)


def test_settings_show_their_source_and_the_environment_locks_them(monkeypatch):
    monkeypatch.setenv("ONCUE_WORKERS", "4")
    monkeypatch.setenv("BAZARR_API_KEY", "secret-key")
    client = local_client()
    view = client.get("/api/settings").json()
    assert setting(view, "workers") == {**setting(view, "workers"), "value": 4, "source": "environment", "locked": True}
    assert setting(view, "bazarr_api_key")["value"] is None and setting(view, "bazarr_api_key")["is_set"] is True
    assert setting(view, "mode")["value"] == "report" and setting(view, "mode")["source"] == "default"
    assert {item["name"] for item in view["fixed"]} == {"allowed_folders", "automatic_fix_confidence"}
    assert client.patch("/api/settings", json={"changes": {"workers": 3}}).json()["code"] == "invalid_setting"
    changed = client.patch("/api/settings", json={"changes": {"mode": "fix", "keep_days": 10}}).json()
    assert setting(changed, "mode")["value"] == "fix" and setting(changed, "mode")["source"] == "settings" and configuration.value("keep_days") == 10
    assert oct(os.stat(storage.state_path("settings.json")).st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("change", [{"keep_days": -1}, {"mode": "sometimes"}, {"webhook_url": "file:///etc/passwd"}, {"url_base": "/a/../b"}, {"nothing": 1},
                                    {"workers": True}, {"watched_folders": ["relative/path"]}])
def test_invalid_settings_are_refused_and_nothing_is_saved(change):
    client = local_client()
    assert client.patch("/api/settings", json={"changes": change}).status_code == 422
    assert not os.path.exists(storage.state_path("settings.json"))


def test_watched_folders_must_be_inside_the_allowed_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [str(tmp_path / "media")])
    client = local_client()
    answer = client.patch("/api/settings", json={"changes": {"watched_folders": [str(tmp_path / "elsewhere")]}})
    assert answer.json()["code"] == "outside_allowed_folders"
    assert client.patch("/api/settings", json={"changes": {"watched_folders": [str(tmp_path / "media" / "movies")]}}).status_code == 200


def test_automatic_audio_writes_need_a_clean_calibration_first():
    client = local_client()
    assert client.patch("/api/settings", json={"changes": {"audio_automatic": True}}).json()["code"] == "not_calibrated"
    os.makedirs(storage.state_path("calibration"))
    with open(storage.state_path("calibration", "audio-2026-10-29T10-00-00.json"), "w") as handle:
        json.dump({"count": 40, "false_positives": 0}, handle)
    view = client.patch("/api/settings", json={"changes": {"audio_automatic": True}}).json()
    assert setting(view, "audio_automatic")["value"] is True and "no wrong confident result in 40 trials" in setting(view, "audio_automatic")["note"]


def test_an_api_key_opens_the_api_without_a_session_and_can_be_replaced(monkeypatch):
    monkeypatch.setenv("ONCUE_UI_PASSWORD", PASSWORD)
    client = TestClient(create_app("0.0.0.0"), base_url="http://guard.example")
    assert client.get("/api/status").status_code == 401 and client.post("/api/settings/api-key/reveal", json={"password": PASSWORD}).status_code == 401
    csrf = client.post("/api/session", json={"user": "admin", "password": PASSWORD}).json()["csrf"]
    key = client.post("/api/settings/api-key/reveal", json={"password": PASSWORD}, headers={CSRF_HEADER: csrf}).json()["key"]
    outsider = TestClient(client.app, base_url="http://guard.example")
    assert outsider.get("/api/status", headers={API_KEY_HEADER: key}).status_code == 200
    assert outsider.patch("/api/settings", json={"changes": {"keep_days": 7}}, headers={API_KEY_HEADER: key}).status_code == 200
    new_key = client.post("/api/settings/api-key", json={"password": PASSWORD}, headers={CSRF_HEADER: csrf}).json()["key"]
    assert new_key != key and outsider.get("/api/status", headers={API_KEY_HEADER: key}).status_code == 401


def test_with_an_account_the_key_needs_the_password_and_wrong_ones_count_toward_the_lockout(monkeypatch):
    monkeypatch.setenv("ONCUE_UI_PASSWORD", PASSWORD)
    client = TestClient(create_app("0.0.0.0"), base_url="http://guard.example")
    client.headers.update({CSRF_HEADER: client.post("/api/session", json={"user": "admin", "password": PASSWORD}).json()["csrf"]})
    key = auth.api_key()
    refused = client.post("/api/settings/api-key/reveal", json={})
    assert refused.status_code == 403 and refused.json()["code"] == "wrong_password"
    assert client.post("/api/settings/api-key", json={"password": "wrong"}).status_code == 403 and auth.api_key() == key
    for _ in range(auth.MAX_FAILED_LOGINS - 2):
        client.post("/api/settings/api-key/reveal", json={"password": "wrong"})
    assert client.post("/api/settings/api-key/reveal", json={"password": PASSWORD}).status_code == 429


def test_without_an_account_the_key_shows_and_changes_without_a_password(monkeypatch):
    client = local_client()
    assert client.post("/api/settings/api-key/reveal", json={}).json()["key"] == auth.api_key()
    assert client.post("/api/settings/api-key", json={}).json()["key"] == auth.api_key()
    monkeypatch.setenv("ONCUE_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("ONCUE_AUTH", "local")
    neighbour = TestClient(create_app("0.0.0.0"), base_url="http://guard.lan", client=("192.168.1.20", 50000))
    neighbour.headers.update({CSRF_HEADER: neighbour.get("/api/session").json()["csrf"]})
    assert neighbour.post("/api/settings/api-key/reveal", json={}).json()["key"] == auth.api_key()


def test_local_addresses_can_skip_the_login_only_when_asked(monkeypatch):
    monkeypatch.setenv("ONCUE_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("ONCUE_AUTH", "local")
    app = create_app("0.0.0.0")
    neighbour = TestClient(app, base_url="http://guard.lan", client=("192.168.1.20", 50000))
    assert neighbour.get("/api/status").status_code == 200 and neighbour.get("/api/session").json()["login_required"] is False
    assert TestClient(app, base_url="http://192.168.1.5:8484", client=("192.168.1.20", 50000)).get("/api/status").status_code == 200
    assert TestClient(app, base_url="http://guard.lan", client=("8.8.8.8", 50000)).get("/api/status").status_code == 401


def test_local_mode_lets_no_one_in_through_a_proxy_or_a_rebound_name(monkeypatch):
    monkeypatch.setenv("ONCUE_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("ONCUE_AUTH", "local")
    app = create_app("0.0.0.0")
    proxied = TestClient(app, base_url="https://subtitles.example.com", client=("172.17.0.1", 50000))
    assert proxied.post("/api/settings/api-key/reveal", json={}, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 401
    assert proxied.get("/api/session", headers={"X-Forwarded-For": "8.8.8.8"}).json() == {"login_required": True, "user": None, "csrf": None}
    rebound = TestClient(app, base_url="http://attacker.example", client=("192.168.1.20", 50000))
    assert rebound.post("/api/settings/api-key/reveal", json={}).status_code == 401
    forged = {"Cookie": "oncue_csrf=chosen", CSRF_HEADER: "chosen"}
    assert rebound.patch("/api/settings", json={"changes": {"keep_days": 7}}, headers=forged).status_code == 401


def test_local_mode_behind_a_trusted_proxy_judges_the_real_client(monkeypatch):
    monkeypatch.setenv("ONCUE_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("ONCUE_AUTH", "local")
    monkeypatch.setenv("ONCUE_TRUSTED_PROXIES", "172.17.0.0/16")
    proxy = TestClient(create_app("0.0.0.0"), base_url="http://guard.lan", client=("172.17.0.1", 50000))
    assert proxy.get("/api/status", headers={"X-Forwarded-For": "192.168.1.20"}).status_code == 200
    assert proxy.get("/api/status", headers={"X-Forwarded-For": "192.168.1.20, 8.8.8.8"}).status_code == 401


def test_external_authentication_trusts_only_the_listed_proxies(monkeypatch):
    monkeypatch.setenv("ONCUE_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("ONCUE_AUTH", "external")
    monkeypatch.setenv("ONCUE_TRUSTED_PROXIES", "10.0.0.2")
    app = create_app("0.0.0.0")
    through_proxy = TestClient(app, base_url="https://subtitles.example.com", client=("10.0.0.2", 50000))
    assert through_proxy.get("/api/status", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200
    direct = TestClient(app, base_url="http://192.168.1.5:8484", client=("192.168.1.30", 50000))
    assert direct.get("/api/status").status_code == 401 and direct.get("/api/session").json()["login_required"] is True


def test_a_wrong_trusted_proxy_list_stops_the_start(monkeypatch):
    monkeypatch.setenv("ONCUE_TRUSTED_PROXIES", "not an address")
    with pytest.raises(ConfigError):
        create_app("127.0.0.1")


def test_a_new_bazarr_address_needs_its_key_again(monkeypatch):
    client = local_client()
    refused = client.patch("/api/settings", json={"changes": {"bazarr_url": "http://attacker.example:6767"}})
    assert refused.status_code == 422 and refused.json()["code"] == "bazarr_key_needed"
    assert client.patch("/api/settings", json={"changes": {"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "new-key"}}).status_code == 200


def test_everything_moves_under_the_url_base_but_the_health_probes(monkeypatch):
    monkeypatch.setenv("ONCUE_URL_BASE", "/oncue")
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    assert client.get("/oncue/api/status").status_code == 200 and client.get("/api/status").status_code == 404
    assert client.get("/health").status_code == 200 and client.get("/oncue/ready").status_code in (200, 503)
    assert client.get("/oncue/api/openapi.json").status_code == 200
