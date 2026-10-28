# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP server: authentication, sessions, CSRF and origin checks, limits and security headers, then the page routes."""

import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import jobs
from ..heartbeats import worker_health
from ..logs import (
    LEVEL,
    log,
    settings,
)
from ..storage import state_path
from .actions import perform
from .context import AUTH, REQUEST, SESSIONS, TOKEN, notice
from .pages import (
    api_jobs,
    api_log,
    api_review,
    api_status,
    dashboard_page,
    history_page,
    log_page,
    raw_log,
    review_page,
    subtitle_page,
)
from .parts import escape, layout

HOST = os.environ.get("SUBTITLE_GUARD_UI_HOST", "127.0.0.1")


PORT = int(os.environ.get("SUBTITLE_GUARD_UI_PORT", "8484"))


SESSION_COOKIE = "subtitle_guard_session"


CSRF_COOKIE = "subtitle_guard_csrf"


SESSION_SECONDS = 30 * 24 * 3600


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


MAX_BODY_BYTES = 64 * 1024


MAX_FIELDS = 50


MAX_CONNECTIONS = 32


REQUEST_TIMEOUT_SECONDS = 30


FAILED_LOGIN_LOG_SECONDS = 60


SECURITY_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; "
                                               "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}


FAILED_LOGINS = {}


def serve_ui(host=HOST, port=PORT):
    settings["source"] = "web"
    AUTH["token"] = TOKEN or ("" if host in LOOPBACK_HOSTS else generated_token(host))
    server = Server((host, port), Handler)
    log("web.listening", f"HTTP listening on {host}:{server.server_address[1]} auth={'token' if AUTH['token'] else 'none'}", host=host,
        port=server.server_address[1], token_required=bool(AUTH["token"]))
    server.serve_forever()


def generated_token(host):
    path = state_path("ui-token")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(secrets.token_urlsafe(24))
        log("web.token-generated", f"SUBTITLE_GUARD_UI_TOKEN unset while listening on {host}, token generated in {path}", LEVEL["warning"], path=path)
    with open(path) as handle:
        token = handle.read().strip()
    print(f"subtitle-guard: open the UI with the token {token} (stored in {path})", flush=True)
    return token


class Server(ThreadingHTTPServer):
    daemon_threads = True
    connections = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()


class Handler(BaseHTTPRequestHandler):
    timeout = REQUEST_TIMEOUT_SECONDS

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(url.query, max_num_fields=MAX_FIELDS).items()}
        if url.path in ("/health", "/ready"):
            return self.respond(*(health_page() if url.path == "/health" else ready_page()))
        if not self.trusted_host():
            return self.respond(400, "unknown host", "text/plain; charset=utf-8")
        self.start_request()
        if not self.authorised():
            log("web.auth-denied", f"401 GET {url.path} from {self.client_address[0]}", LEVEL["warning"], method="GET", path=url.path, client=self.client_address[0])
            return self.respond(401, login_page())
        if url.path not in PAGES:
            return self.respond(404, layout("Not found", "<p>Nothing here.</p>"))
        try:
            status, body, content_type = PAGES[url.path](query)
        except Exception as error:
            log("web.error", f"GET {url.path} failed: {type(error).__name__}: {error}", LEVEL["error"], path=url.path, trace=traceback.format_exc(limit=8))
            return self.respond(500, layout("Something went wrong", "<p>This page failed. The details are in the Log.</p>"))
        self.respond(status, body, content_type)

    def do_POST(self):
        if not self.trusted_host() or not self.same_origin():
            log("web.request-refused", f"403 POST {self.path} from {self.client_address[0]}: foreign origin or host", LEVEL["warning"], path=self.path,
                client=self.client_address[0], origin=self.headers.get("Origin"), host=self.headers.get("Host"))
            return self.respond(403, "forbidden", "text/plain; charset=utf-8")
        form = self.read_form()
        if form is None:
            return self.respond(413, "request too large or malformed", "text/plain; charset=utf-8")
        self.start_request()
        if self.path == "/login":
            return self.login(form)
        if not self.authorised():
            log("web.auth-denied", f"401 POST {self.path} from {self.client_address[0]}", LEVEL["warning"], method="POST", path=self.path, client=self.client_address[0])
            return self.respond(401, login_page())
        if not form.get("csrf") or not hmac.compare_digest(form["csrf"], self.cookies().get(CSRF_COOKIE, "")):
            return self.respond(403, layout("Form expired", '<p>This form expired. <a href="/">Reload the page</a> and try again.</p>'))
        if self.path == "/logout":
            SESSIONS.discard(self.cookies().get(SESSION_COOKIE, ""))
            return self.redirect("/", [f"{SESSION_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"])
        try:
            location = perform(form)
        except Exception as error:
            log("web.error", f"POST action={form.get('action')} failed: {type(error).__name__}: {error}", LEVEL["error"], action=form.get("action"),
                subtitle=form.get("path"), trace=traceback.format_exc(limit=8))
            location = "/review?" + urllib.parse.urlencode({"notice": notice("That action failed and nothing more was done. The details are in the Log.")})
        self.redirect(location)

    def start_request(self):
        REQUEST.cache = {}
        REQUEST.csrf = self.cookies().get(CSRF_COOKIE) or secrets.token_urlsafe(24)
        REQUEST.new_csrf = CSRF_COOKIE not in self.cookies()

    def read_form(self):
        length = self.headers.get("Content-Length", "0")
        if not length.isdigit() or int(length) > MAX_BODY_BYTES:
            return None
        body = self.rfile.read(int(length)).decode(errors="replace")
        return {key: values[0] for key, values in urllib.parse.parse_qs(body, max_num_fields=MAX_FIELDS).items()}

    def login(self, form):
        if not AUTH["token"]:
            return self.redirect("/")
        if not hmac.compare_digest(form.get("token", ""), AUTH["token"]):
            log_failed_login(self.client_address[0])
            return self.respond(401, login_page("That token is not right."))
        session = secrets.token_urlsafe(32)
        SESSIONS.add(session)
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        log("web.login", f"session opened from {self.client_address[0]}", client=self.client_address[0])
        self.redirect("/", [f"{SESSION_COOKIE}={session}; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict; Path=/{secure}"])

    def authorised(self):
        return not AUTH["token"] or self.cookies().get(SESSION_COOKIE, "") in SESSIONS

    def trusted_host(self):
        if AUTH["token"]:
            return True
        return host_name(self.headers.get("Host", "")) in LOOPBACK_HOSTS

    def same_origin(self):
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if not origin:
            return True
        return urllib.parse.urlparse(origin).netloc == self.headers.get("Host", "")

    def cookies(self):
        return dict(part.strip().split("=", 1) for part in self.headers.get("Cookie", "").split(";") if "=" in part)

    def respond(self, status, body, content_type="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_common_headers()
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, cookies=()):
        self.send_response(303)
        self.send_header("Location", location)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_common_headers()
        self.end_headers()

    def send_common_headers(self):
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if getattr(REQUEST, "new_csrf", False):
            self.send_header("Set-Cookie", f"{CSRF_COOKIE}={REQUEST.csrf}; HttpOnly; SameSite=Strict; Path=/")
            REQUEST.new_csrf = False

    def log_message(self, format, *arguments):
        return


def log_failed_login(client):
    now_seconds = time.time()
    if now_seconds - FAILED_LOGINS.get(client, 0) < FAILED_LOGIN_LOG_SECONDS:
        return
    FAILED_LOGINS[client] = now_seconds
    log("web.login-failed", f"wrong token from {client}", LEVEL["warning"], client=client)


def host_name(host):
    if host.startswith("["):
        return host[1:host.find("]")]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def login_page(problem=""):
    warning = f'<p class="message">{escape(problem)}</p>' if problem else ""
    return layout("Token needed", f'{warning}<form method="post" action="/login" class="filters"><label class="visually-hidden" for="token">Token</label>'
                                  '<input type="password" id="token" name="token" placeholder="token" autocomplete="current-password"><button>Open</button></form>'
                                  '<p class="subtle words">The token is in SUBTITLE_GUARD_UI_TOKEN, or printed when the service starts.</p>', navigation=False)


def health_page():
    return 200, "ok", "text/plain; charset=utf-8"


def ready_page():
    try:
        jobs.counts()
    except sqlite3.Error as error:
        return 503, json.dumps({"ready": False, "database": f"{type(error).__name__}: {error}"}), "application/json"
    workers = worker_health()
    ready = all(workers.values())
    return (200 if ready else 503), json.dumps({"ready": ready, "database": "ok", "workers": workers}), "application/json"


PAGES = {"/": dashboard_page, "/review": review_page, "/history": history_page, "/jobs": history_page, "/log": log_page, "/log.jsonl": raw_log,
         "/subtitle": subtitle_page, "/api/status": api_status, "/api/jobs": api_jobs, "/api/review": api_review, "/api/log": api_log}
