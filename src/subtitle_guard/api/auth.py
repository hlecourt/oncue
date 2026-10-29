# SPDX-License-Identifier: GPL-3.0-or-later
"""UI account: one user with a scrypt password hash, sessions that expire on the server, and a limit on failed logins."""

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time

from ..errors import ConfigError
from ..logs import LEVEL, log
from ..storage import state_path

CREDENTIALS_FILE = "ui-credentials.json"
DEFAULT_USER = "admin"
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
MINIMUM_PASSWORD_LENGTH = 12
SESSION_SECONDS = 30 * 24 * 3600
MAX_SESSIONS = 1000
MAX_FAILED_LOGINS = 5
MAX_TRACKED_CLIENTS = 10000
LOCKOUT_SECONDS = 600
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
ACCOUNT = {}
SESSIONS = {}
FAILURES = {}


def configure(host):
    password = os.environ.get("SUBTITLE_GUARD_UI_PASSWORD", "")
    if password:
        ACCOUNT.clear()
        ACCOUNT.update(account(os.environ.get("SUBTITLE_GUARD_UI_USER", DEFAULT_USER), password), source="environment")
        return True
    if os.path.exists(state_path(CREDENTIALS_FILE)):
        load_credentials()
        return True
    if host in LOOPBACK_HOSTS:
        ACCOUNT.clear()
        return False
    generated = secrets.token_urlsafe(18)
    save_credentials(DEFAULT_USER, generated)
    print(f"subtitle-guard: first start, log in to the UI as {DEFAULT_USER} with the password {generated} "
          "(shown once; change it with: subtitle-guard set-password)", flush=True)
    log("web.password-generated", f"no UI password set while listening on {host}, one was generated for {DEFAULT_USER} and printed once on stdout",
        LEVEL["warning"], path=state_path(CREDENTIALS_FILE))
    return True


def account(user, password):
    salt = secrets.token_bytes(16)
    return {"user": user, "salt": salt.hex(), "hash": scrypt(password, salt, SCRYPT).hex(), **SCRYPT}


def scrypt(password, salt, parameters):
    return hashlib.scrypt(password.encode(), salt=salt, n=parameters["n"], r=parameters["r"], p=parameters["p"], dklen=32)


def save_credentials(user, password):
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ConfigError(f"the password must have at least {MINIMUM_PASSWORD_LENGTH} characters")
    path = state_path(CREDENTIALS_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".ui-credentials.", dir=os.path.dirname(path))
    with os.fdopen(descriptor, "w") as handle:
        json.dump(account(user, password), handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    load_credentials()


def load_credentials():
    path = state_path(CREDENTIALS_FILE)
    try:
        with open(path) as handle:
            stored = json.load(handle)
        loaded = {key: stored[key] for key in ("user", "salt", "hash", "n", "r", "p")}
        bytes.fromhex(loaded["salt"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ConfigError(f"{path} is unreadable, refusing to serve the UI; run subtitle-guard set-password: {error}") from error
    if not loaded["user"] or len(loaded["hash"]) != 64:
        raise ConfigError(f"{path} has no usable account, refusing to serve the UI; run subtitle-guard set-password")
    ACCOUNT.clear()
    ACCOUNT.update(loaded, source="file", modified=os.stat(path).st_mtime_ns)
    SESSIONS.clear()


def current_account():
    if ACCOUNT.get("source") == "file" and os.stat(state_path(CREDENTIALS_FILE)).st_mtime_ns != ACCOUNT["modified"]:
        load_credentials()
    return ACCOUNT


def verify(user, password):
    stored = current_account()
    given = scrypt(password, bytes.fromhex(stored["salt"]), stored)
    return hmac.compare_digest(given.hex(), stored["hash"]) and hmac.compare_digest(user.encode(), stored["user"].encode())


def locked_out(client):
    recent = [moment for moment in FAILURES.pop(client, []) if time.time() - moment < LOCKOUT_SECONDS]
    if recent:
        FAILURES[client] = recent
    return len(recent) >= MAX_FAILED_LOGINS


def record_failure(client):
    if client not in FAILURES and len(FAILURES) >= MAX_TRACKED_CLIENTS:
        FAILURES.pop(next(iter(FAILURES)))
    FAILURES.setdefault(client, []).append(time.time())
    log("web.login-failed", f"wrong user or password from {client} ({len(FAILURES[client])} in {LOCKOUT_SECONDS // 60} min)", LEVEL["warning"], client=client)


def open_session(user):
    for session in [session for session, opened in SESSIONS.items() if opened["expires"] < time.time()]:
        SESSIONS.pop(session)
    while len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(next(iter(SESSIONS)))
    session = secrets.token_urlsafe(32)
    SESSIONS[session] = {"user": user, "csrf": secrets.token_urlsafe(24), "expires": time.time() + SESSION_SECONDS}
    return session


def session_for(session):
    opened = SESSIONS.get(session)
    if opened is None or opened["expires"] < time.time():
        SESSIONS.pop(session, None)
        return None
    return opened
