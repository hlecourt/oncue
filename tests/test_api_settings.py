# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os

import pytest
from fastapi.testclient import TestClient

from subtitle_guard import configuration, files, storage
from subtitle_guard.api.app import create_app
from subtitle_guard.api.security import API_KEY_HEADER, CSRF_HEADER

PASSWORD = "a long enough password"


def local_client():
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1")
    client.headers.update({CSRF_HEADER: client.get("/api/session").json()["csrf"]})
    return client


def setting(view, name):
    return next(item for item in view["settings"] if item["name"] == name)


def test_settings_show_their_source_and_the_environment_locks_them(monkeypatch):
    monkeypatch.setenv("SUBTITLE_GUARD_WORKERS", "4")
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
    monkeypatch.setattr(files, "ROOTS", [str(tmp_path / "media")])
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
    monkeypatch.setenv("SUBTITLE_GUARD_UI_PASSWORD", PASSWORD)
    client = TestClient(create_app("0.0.0.0"), base_url="http://guard.example")
    assert client.get("/api/status").status_code == 401 and client.get("/api/settings/api-key").status_code == 401
    csrf = client.post("/api/session", json={"user": "admin", "password": PASSWORD}).json()["csrf"]
    key = client.get("/api/settings/api-key").json()["key"]
    outsider = TestClient(client.app, base_url="http://guard.example")
    assert outsider.get("/api/status", headers={API_KEY_HEADER: key}).status_code == 200
    assert outsider.patch("/api/settings", json={"changes": {"keep_days": 7}}, headers={API_KEY_HEADER: key}).status_code == 200
    new_key = client.post("/api/settings/api-key", headers={CSRF_HEADER: csrf}).json()["key"]
    assert new_key != key and outsider.get("/api/status", headers={API_KEY_HEADER: key}).status_code == 401


def test_local_addresses_can_skip_the_login_only_when_asked(monkeypatch):
    monkeypatch.setenv("SUBTITLE_GUARD_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("SUBTITLE_GUARD_AUTH", "local")
    app = create_app("0.0.0.0")
    assert TestClient(app, base_url="http://guard.lan", client=("192.168.1.20", 50000)).get("/api/status").status_code == 200
    assert TestClient(app, base_url="http://guard.lan", client=("8.8.8.8", 50000)).get("/api/status").status_code == 401


def test_external_authentication_trusts_the_reverse_proxy(monkeypatch):
    monkeypatch.setenv("SUBTITLE_GUARD_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("SUBTITLE_GUARD_AUTH", "external")
    client = TestClient(create_app("0.0.0.0"), base_url="https://subtitles.example.com")
    assert client.get("/api/status", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200


def test_everything_moves_under_the_url_base_but_the_health_probes(monkeypatch):
    monkeypatch.setenv("SUBTITLE_GUARD_URL_BASE", "/subtitle-guard")
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1")
    assert client.get("/subtitle-guard/api/status").status_code == 200 and client.get("/api/status").status_code == 404
    assert client.get("/health").status_code == 200 and client.get("/subtitle-guard/ready").status_code in (200, 503)
    assert client.get("/subtitle-guard/api/openapi.json").status_code == 200
