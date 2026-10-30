# SPDX-License-Identifier: GPL-3.0-or-later
"""Who may call the API: the host and origin checks, request limits and security headers for every response, and the session and CSRF checks
the routes depend on."""

import asyncio
import hmac
import ipaddress
import secrets
import threading
import urllib.parse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from ..configuration import AUTH_MODE
from .auth import LOCKOUT_SECONDS, LOOPBACK_HOSTS, api_key, locked_out, record_failure, record_success, session_for, verify
from .i18n import current_locale, locale_from, tr

SESSION_COOKIE = "subtitle_guard_session"
CSRF_HEADER = "X-CSRF-Token"
API_KEY_HEADER = "X-Api-Key"
MAX_BODY_BYTES = 64 * 1024
BODY_SECONDS = 30
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")
LOGIN_LOCK = threading.Lock()
LOCAL_HOST_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")
SECURITY_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
                    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                                                "media-src 'self' blob:; font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
                                                "base-uri 'self'")}


async def guard(request, call_next):
    current_locale.set(locale_from(request.headers.get("accept-language")))
    if request.url.path not in request.app.state.open_paths and not trusted_host(request):
        return refusal(400, "unknown_host", tr("This host is not allowed."))
    if request.method not in SAFE_METHODS:
        if not same_origin(request):
            return refusal(403, "foreign_origin", tr("Requests from another site are refused."))
        if "transfer-encoding" in request.headers:
            return refusal(411, "length_required", tr("Send the request with a Content-Length."))
        length = request.headers.get("content-length", "0")
        if not (length.isascii() and length.isdigit()) or int(length) > MAX_BODY_BYTES:
            return refusal(413, "too_large", tr("The request is too large."))
        try:
            await asyncio.wait_for(request.body(), BODY_SECONDS)
        except TimeoutError:
            return refusal(408, "too_slow", tr("The request took too long to arrive."))
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        if name not in response.headers:
            response.headers[name] = value
    return response


def trusted_host(request):
    if request.app.state.login_required or (request.app.state.auth_mode == AUTH_MODE["external"] and from_trusted_proxy(request)):
        return True
    peer = ip_or_none(request.client.host if request.client else "")
    return host_name(request.headers.get("host", "")) in LOOPBACK_HOSTS and peer is not None and peer.is_loopback and not forwarded(request)


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
    if not request.app.state.login_required:
        return False
    if request.app.state.auth_mode == AUTH_MODE["external"]:
        return not from_trusted_proxy(request)
    if request.app.state.auth_mode == AUTH_MODE["local"]:
        return not local_client(request)
    return True


def local_client(request):
    if forwarded(request) and not from_trusted_proxy(request):
        return False
    address = ip_or_none(client_address(request))
    return address is not None and (address.is_private or address.is_loopback) and local_host(host_name(request.headers.get("host", "")))


def local_host(name):
    if ip_or_none(name) is not None or name in LOOPBACK_HOSTS:
        return True
    return "." not in name or name.endswith(LOCAL_HOST_SUFFIXES)


def client_address(request):
    peer = request.client.host if request.client else ""
    if not from_trusted_proxy(request):
        return peer
    hops = [hop.strip() for hop in request.headers.get("x-forwarded-for", "").split(",") if hop.strip()]
    untrusted = [hop for hop in hops if not trusted_proxy(request, hop)]
    return untrusted[-1] if untrusted else peer


def from_trusted_proxy(request):
    return trusted_proxy(request, request.client.host if request.client else "")


def trusted_proxy(request, address):
    parsed = ip_or_none(address)
    return parsed is not None and any(parsed in network for network in request.app.state.trusted_proxies)


def forwarded(request):
    return any(request.headers.get(name) for name in PROXY_HEADERS)


def ip_or_none(text):
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    return address.ipv4_mapped if address.version == 6 and address.ipv4_mapped else address


def same_secret(given, expected):
    return bool(given) and bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def valid_api_key(request):
    given = request.headers.get(API_KEY_HEADER, "")
    return bool(given) and same_secret(given, api_key())


def expected_csrf(request):
    session = current_session(request)
    if session:
        return session["csrf"]
    if login_needed(request):
        return ""
    return request.app.state.anonymous_csrf


def require_user(request: Request):
    if valid_api_key(request):
        return
    if login_needed(request) and current_session(request) is None:
        raise HTTPException(401, {"code": "login_required", "message": tr("Log in first.")})


def require_csrf(request: Request):
    require_user(request)
    if valid_api_key(request):
        return
    if not same_secret(request.headers.get(CSRF_HEADER, ""), expected_csrf(request)):
        raise HTTPException(403, {"code": "csrf", "message": tr("The page expired, reload it and try again.")})
    has_body = request.headers.get("content-length", "0") not in ("", "0")
    if has_body and request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, {"code": "json_only", "message": tr("Send JSON.")})


def password_accepted(request, user, password):
    client = client_address(request)
    with LOGIN_LOCK:
        if locked_out(client):
            raise HTTPException(429, {"code": "locked_out", "message": tr("Too many wrong attempts. Wait {minutes} minutes and try again.", minutes=LOCKOUT_SECONDS // 60)})
        if not verify(user, password):
            record_failure(client)
            return False
    record_success(client)
    return True


def new_csrf_token():
    return secrets.token_urlsafe(24)
