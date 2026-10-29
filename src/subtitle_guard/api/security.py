# SPDX-License-Identifier: GPL-3.0-or-later
"""Who may call the API: the host and origin checks, request limits and security headers for every response, and the session and CSRF checks
the routes depend on."""

import hmac
import ipaddress
import os
import secrets
import urllib.parse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from ..configuration import AUTH_MODE
from ..storage import state_path
from .auth import LOOPBACK_HOSTS, session_for

SESSION_COOKIE = "subtitle_guard_session"
CSRF_COOKIE = "subtitle_guard_csrf"
CSRF_HEADER = "X-CSRF-Token"
API_KEY_HEADER = "X-Api-Key"
API_KEY_FILE = "api-key"
MAX_BODY_BYTES = 64 * 1024
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")
SECURITY_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
                    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                                                "media-src 'self' blob:; font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
                                                "base-uri 'self'")}


async def guard(request, call_next):
    if request.url.path not in request.app.state.open_paths and not trusted_host(request):
        return refusal(400, "unknown_host", "This host is not allowed.")
    if request.method not in SAFE_METHODS:
        if not same_origin(request):
            return refusal(403, "foreign_origin", "Requests from another site are refused.")
        length = request.headers.get("content-length", "0")
        if not (length.isascii() and length.isdigit()) or int(length) > MAX_BODY_BYTES:
            return refusal(413, "too_large", "The request is too large.")
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        if name not in response.headers:
            response.headers[name] = value
    return response


def trusted_host(request):
    if request.app.state.login_required or request.app.state.auth_mode == AUTH_MODE["external"]:
        return True
    return host_name(request.headers.get("host", "")) in LOOPBACK_HOSTS and not any(request.headers.get(name) for name in PROXY_HEADERS)


def same_origin(request):
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True
    return urllib.parse.urlparse(origin).netloc == request.headers.get("host", "")


def host_name(host):
    if host.startswith("["):
        return host[1:host.find("]")]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def refusal(status, code, message):
    response = JSONResponse({"code": code, "message": message}, status_code=status)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


def current_session(request: Request):
    return session_for(request.cookies.get(SESSION_COOKIE, "")) if request.app.state.login_required else None


def login_needed(request):
    if not request.app.state.login_required or request.app.state.auth_mode == AUTH_MODE["external"]:
        return False
    return not (request.app.state.auth_mode == AUTH_MODE["local"] and local_client(request))


def local_client(request):
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def valid_api_key(request):
    given = request.headers.get(API_KEY_HEADER, "")
    return bool(given) and hmac.compare_digest(given, api_key())


def api_key():
    path = state_path(API_KEY_FILE)
    if not os.path.exists(path):
        return regenerate_api_key()
    with open(path) as handle:
        return handle.read().strip()


def regenerate_api_key():
    path = state_path(API_KEY_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = secrets.token_hex(16)
    descriptor = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(key)
    os.replace(path + ".new", path)
    return key


def expected_csrf(request):
    session = current_session(request)
    if session:
        return session["csrf"]
    return request.cookies.get(CSRF_COOKIE) or ""


def require_user(request: Request):
    if valid_api_key(request):
        return
    if login_needed(request) and current_session(request) is None:
        raise HTTPException(401, {"code": "login_required", "message": "Log in first."})


def require_csrf(request: Request):
    require_user(request)
    if valid_api_key(request):
        return
    expected = expected_csrf(request)
    given = request.headers.get(CSRF_HEADER, "")
    if not expected or not given or not hmac.compare_digest(given, expected):
        raise HTTPException(403, {"code": "csrf", "message": "The page expired, reload it and try again."})
    has_body = request.headers.get("content-length", "0") not in ("", "0")
    if has_body and request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, {"code": "json_only", "message": "Send JSON."})


def new_csrf_token():
    return secrets.token_urlsafe(24)
