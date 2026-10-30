# SPDX-License-Identifier: GPL-3.0-or-later
import ipaddress
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from subtitle_guard import bazarr, configuration, notifications, storage
from subtitle_guard.api import auth
from subtitle_guard.api.app import create_app
from subtitle_guard.api.security import CSRF_HEADER, SESSION_COOKIE, ip_or_none
from subtitle_guard.errors import ConfigError

PASSWORD = "a long enough password"


@pytest.fixture
def protected(monkeypatch):
    monkeypatch.setenv("SUBTITLE_GUARD_UI_PASSWORD", PASSWORD)
    return TestClient(create_app("0.0.0.0"), base_url="http://guard.example:8484")


@pytest.fixture
def answering_server():
    class Answer(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            self.do_GET()

        def log_message(self, *arguments):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def local_client():
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    client.headers.update({CSRF_HEADER: client.get("/api/session").json()["csrf"]})
    return client


def test_the_key_in_bazarr_config_never_goes_to_an_address_set_in_settings(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("auth:\n  apikey: real-key\n")
    monkeypatch.setattr(bazarr, "BAZARR_CONFIG", str(config))
    monkeypatch.delenv("BAZARR_API_KEY", raising=False)
    monkeypatch.delenv("BAZARR_URL", raising=False)
    assert bazarr.api_key() == "real-key"
    client = local_client()
    assert client.patch("/api/settings", json={"changes": {"bazarr_url": "http://attacker.example", "bazarr_api_key": "x"}}).status_code == 200
    emptied = client.patch("/api/settings", json={"changes": {"bazarr_api_key": ""}})
    assert emptied.status_code == 422 and emptied.json()["code"] == "bazarr_key_needed"
    configuration.update({"bazarr_api_key": ""})
    with pytest.raises(ConfigError):
        bazarr.api_key()


@pytest.mark.parametrize("address", ["169.254.169.254", "::ffff:169.254.169.254", "fe80::1", "fd00:ec2::254"])
def test_link_local_and_cloud_metadata_peers_are_refused(address):
    class Connection:
        sock = type("Socket", (), {"getpeername": lambda self: (address, 80)})()
        closed = False

        def close(self):
            self.closed = True
    connection = Connection()
    with pytest.raises(ConfigError):
        configuration.refuse_link_local_peer(connection)
    assert connection.closed


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.1.10", "::1", "10.0.0.5"])
def test_loopback_and_lan_peers_stay_allowed(address):
    connection = type("Connection", (), {"sock": type("Socket", (), {"getpeername": lambda self: (address, 80)})()})()
    configuration.refuse_link_local_peer(connection)


def test_bazarr_and_the_webhook_check_the_address_they_actually_connect_to(answering_server, monkeypatch):
    monkeypatch.setenv("BAZARR_URL", answering_server)
    monkeypatch.setenv("BAZARR_API_KEY", "key")
    assert bazarr.bazarr_get("/api/movies") == {"data": []}
    assert notifications.post(answering_server, {"count": 0})
    monkeypatch.setattr(configuration, "CLOUD_METADATA_ADDRESSES", (ipaddress.ip_address("127.0.0.1"),))
    with pytest.raises(ConfigError):
        bazarr.bazarr_get("/api/movies")
    assert not notifications.post(answering_server, {"count": 0})


def test_an_ipv4_mapped_address_is_judged_as_the_ipv4_address(monkeypatch):
    assert ip_or_none("::ffff:10.0.0.5") == ipaddress.ip_address("10.0.0.5")
    monkeypatch.setenv("SUBTITLE_GUARD_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("SUBTITLE_GUARD_AUTH", "external")
    monkeypatch.setenv("SUBTITLE_GUARD_TRUSTED_PROXIES", "10.0.0.0/8")
    app = create_app("0.0.0.0")
    assert TestClient(app, base_url="http://guard.example", client=("::ffff:10.0.0.5", 50000)).get("/api/status").status_code == 200
    assert TestClient(app, base_url="http://guard.example", client=("::ffff:203.0.113.9", 50000)).get("/api/status").status_code == 401


def test_one_account_is_locked_after_many_failures_from_any_number_of_addresses():
    for number in range(auth.MAX_ACCOUNT_FAILED_LOGINS):
        assert not auth.locked_out(f"203.0.113.{number}")
        auth.record_failure(f"203.0.113.{number}")
    assert auth.locked_out("198.51.100.1")
    auth.ACCOUNT_FAILURES[:] = [moment - auth.LOCKOUT_SECONDS for moment in auth.ACCOUNT_FAILURES]
    assert not auth.locked_out("198.51.100.1")


def test_the_account_lock_spares_an_address_that_logged_in_in_the_last_30_days(monkeypatch):
    monkeypatch.setenv("SUBTITLE_GUARD_UI_PASSWORD", PASSWORD)
    app = create_app("0.0.0.0")

    def log_in_from(address, password):
        return TestClient(app, base_url="http://guard.example", client=(address, 50000)).post("/api/session", json={"user": "admin", "password": password})
    assert log_in_from("192.0.2.7", PASSWORD).status_code == 200
    for number in range(auth.MAX_ACCOUNT_FAILED_LOGINS):
        log_in_from(f"203.0.113.{number}", "wrong")
    assert log_in_from("198.51.100.1", PASSWORD).status_code == 429 and log_in_from("192.0.2.7", PASSWORD).status_code == 200
    for _ in range(auth.MAX_FAILED_LOGINS):
        log_in_from("192.0.2.7", "wrong")
    assert log_in_from("192.0.2.7", PASSWORD).status_code == 429
    auth.FAILURES.clear()
    auth.KNOWN_CLIENTS["192.0.2.7"] -= auth.KNOWN_CLIENT_SECONDS
    assert log_in_from("192.0.2.7", PASSWORD).status_code == 429


def test_the_remembered_addresses_stay_bounded(monkeypatch):
    monkeypatch.setattr(auth, "MAX_KNOWN_CLIENTS", 2)
    for client in ("a", "b", "a", "c"):
        auth.record_success(client)
    assert list(auth.KNOWN_CLIENTS) == ["a", "c"]


def test_a_new_password_ends_every_session_and_replaces_the_api_key():
    auth.save_credentials("admin", PASSWORD)
    client = TestClient(create_app("0.0.0.0"), base_url="http://guard.example:8484")
    assert client.post("/api/session", json={"user": "admin", "password": PASSWORD}).status_code == 200
    assert client.get("/api/settings").status_code == 200
    previous_key = auth.api_key()
    path = storage.state_path(auth.CREDENTIALS_FILE)
    modified = os.stat(path).st_mtime_ns
    with open(path, "w") as handle:
        json.dump(auth.account("admin", "another long password"), handle)
    os.utime(path, ns=(modified + 10**9, modified + 10**9))
    assert client.get("/api/settings").status_code == 401
    auth.save_credentials("admin", "a third long password")
    assert auth.api_key() != previous_key


def test_the_secure_cookie_flag_follows_a_forwarded_https_only_from_a_trusted_proxy(monkeypatch):
    monkeypatch.setenv("SUBTITLE_GUARD_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("SUBTITLE_GUARD_TRUSTED_PROXIES", "10.0.0.0/8")
    app = create_app("0.0.0.0")
    login = {"json": {"user": "admin", "password": PASSWORD}, "headers": {"x-forwarded-proto": "https"}}
    direct = TestClient(app, base_url="http://guard.example", client=("203.0.113.9", 50000)).post("/api/session", **login)
    proxied = TestClient(app, base_url="http://guard.example", client=("10.0.0.5", 50000)).post("/api/session", **login)
    assert SESSION_COOKIE in direct.headers["set-cookie"] and "Secure" not in direct.headers["set-cookie"]
    assert "Secure" in proxied.headers["set-cookie"]


def test_the_api_description_needs_a_logged_in_user(protected):
    assert protected.get("/api/openapi.json").status_code == 401
    protected.post("/api/session", json={"user": "admin", "password": PASSWORD})
    assert protected.get("/api/openapi.json").json()["info"]["title"] == "subtitle-guard"
