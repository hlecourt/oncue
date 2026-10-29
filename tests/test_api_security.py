# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from subtitle_guard import storage
from subtitle_guard.api import auth
from subtitle_guard.api.app import create_app
from subtitle_guard.api.security import CSRF_HEADER, SESSION_COOKIE
from subtitle_guard.errors import ConfigError

LOCAL = "http://127.0.0.1"
PASSWORD = "a long enough password"


@pytest.fixture
def local():
    return TestClient(create_app("127.0.0.1"), base_url=LOCAL)


@pytest.fixture
def protected(monkeypatch):
    monkeypatch.setenv("SUBTITLE_GUARD_UI_PASSWORD", PASSWORD)
    return TestClient(create_app("0.0.0.0"), base_url="http://guard.example:8484")


def log_in(client):
    answer = client.post("/api/session", json={"user": "admin", "password": PASSWORD})
    assert answer.status_code == 200
    return answer.json()["csrf"]


def test_loopback_without_a_password_works_only_for_loopback_hosts_without_proxy_headers(local):
    assert local.get("/api/status").status_code == 200
    assert local.get("/api/status", headers={"host": "guard.example"}).status_code == 400
    assert local.get("/api/status", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 400
    assert local.get("/health").text == "ok" and local.get("/health", headers={"host": "anything"}).status_code == 200


def test_a_password_protects_every_route_and_the_session_carries_the_csrf_token(protected):
    assert protected.get("/api/status").status_code == 401
    assert protected.get("/api/session").json() == {"login_required": True, "user": None, "csrf": None}
    assert protected.post("/api/session", json={"user": "admin", "password": "wrong"}).status_code == 401
    csrf = log_in(protected)
    assert protected.get("/api/status").status_code == 200 and protected.get("/api/session").json()["csrf"] == csrf
    assert protected.delete("/api/session").status_code == 403
    assert protected.delete("/api/session", headers={CSRF_HEADER: csrf, "content-type": "application/json"}).status_code == 204
    assert protected.get("/api/status").status_code == 401


def test_failed_logins_lock_the_client_out_and_sessions_expire(protected, monkeypatch):
    for _ in range(auth.MAX_FAILED_LOGINS):
        protected.post("/api/session", json={"user": "admin", "password": "wrong"})
    assert protected.post("/api/session", json={"user": "admin", "password": PASSWORD}).status_code == 429
    auth.FAILURES.clear()
    log_in(protected)
    for opened in auth.SESSIONS.values():
        opened["expires"] = time.time() - 1
    assert protected.get("/api/status").status_code == 401


def test_writes_need_the_token_json_the_same_origin_and_a_small_body(protected):
    csrf = log_in(protected)
    headers = {CSRF_HEADER: csrf, "content-type": "application/json"}
    assert protected.request("DELETE", "/api/session", content=b"a=1", headers={**headers, "content-type": "application/x-www-form-urlencoded"}).status_code == 415
    assert protected.delete("/api/session", headers={**headers, "origin": "https://evil.example"}).status_code == 403
    assert protected.delete("/api/session", headers={**headers, "origin": "null"}).status_code == 403
    assert protected.post("/api/session", content=b"x" * 70000, headers={"content-type": "application/json"}).status_code == 413
    assert protected.delete("/api/session", headers={**headers, CSRF_HEADER: "forged"}).status_code == 403


def test_every_answer_carries_strict_security_headers_and_no_docs_are_served(local):
    answer = local.get("/api/status")
    policy = answer.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    assert answer.headers["x-frame-options"] == "DENY" and answer.headers["cache-control"] == "no-store" and answer.headers["referrer-policy"] == "same-origin"
    assert local.get("/docs").status_code == 404 and local.get("/api/openapi.json").status_code == 200
    assert local.get("/api/nothing").json()["code"]


def test_the_session_cookie_is_http_only_and_strict(protected):
    answer = protected.post("/api/session", json={"user": "admin", "password": PASSWORD})
    cookie = answer.headers["set-cookie"]
    assert SESSION_COOKIE in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie


def test_the_engine_cli_and_bazarr_hook_never_import_the_web_stack():
    blocked = "import sys\nfor name in ('fastapi', 'uvicorn', 'starlette', 'pydantic'):\n    sys.modules[name] = None\n"
    script = blocked + "import subtitle_guard.cli, subtitle_guard.bazarr, subtitle_guard.worker\nprint('ok')"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert result.stdout.strip() == "ok", result.stderr


def test_an_empty_or_broken_credentials_file_refuses_to_serve():
    os.makedirs(storage.state_path(), exist_ok=True)
    open(storage.state_path(auth.CREDENTIALS_FILE), "w").close()
    with pytest.raises(ConfigError):
        create_app("0.0.0.0")
    with pytest.raises(ConfigError):
        create_app("127.0.0.1")


def test_a_first_start_off_loopback_generates_a_password_once(capsys):
    assert create_app("0.0.0.0").state.login_required is True
    password = capsys.readouterr().out.split("with the password ")[1].split(" ")[0]
    assert auth.verify("admin", password) and oct(os.stat(storage.state_path(auth.CREDENTIALS_FILE)).st_mode & 0o777) == "0o600"
    assert create_app("0.0.0.0").state.login_required is True and "with the password" not in capsys.readouterr().out
    assert create_app("127.0.0.1").state.login_required is True
