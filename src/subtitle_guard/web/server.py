# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP server: authentication, sessions, CSRF and origin checks, limits and security headers, then the page routes."""

import hmac
import json
import os
import secrets
import socket
import sqlite3
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import jobs
from ..heartbeats import SUPERVISED_VARIABLE, worker_health
from ..logs import (
    LEVEL,
    log,
    settings,
)
from .actions import perform
from .auth import DEFAULT_USER, LOCKOUT_SECONDS, LOOPBACK_HOSTS, SESSION_SECONDS, SESSIONS, configure, locked_out, open_session, record_failure, session_for, verify
from .context import AUTH, REQUEST, notice
from .editor import api_audio, api_frame, editor_page
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
from .parts import SCRIPT_HASHES, escape, layout

HOST = os.environ.get("SUBTITLE_GUARD_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("SUBTITLE_GUARD_UI_PORT", "8484"))
SESSION_COOKIE = "subtitle_guard_session"
CSRF_COOKIE = "subtitle_guard_csrf"
MAX_BODY_BYTES = 64 * 1024
MAX_FIELDS = 50
MAX_CONNECTIONS = 32
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DEADLINE_SECONDS = 60
PROXY_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP")
SECURITY_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
                    "Content-Security-Policy": f"default-src 'none'; script-src {' '.join(SCRIPT_HASHES)}; style-src 'unsafe-inline'; img-src data: blob:; "
                                               "media-src blob:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}


def serve_ui(host=HOST, port=PORT):
    settings["source"] = "web"
    AUTH["required"] = configure(host)
    server = Server((host, port), Handler)
    log("web.listening", f"HTTP listening on {host}:{server.server_address[1]} auth={'password' if AUTH['required'] else 'none (loopback only)'}", host=host,
        port=server.server_address[1], login_required=AUTH["required"])
    server.serve_forever()


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

    def handle(self):
        deadline = threading.Timer(REQUEST_DEADLINE_SECONDS, self.close_connection_now)
        deadline.daemon = True
        deadline.start()
        try:
            super().handle()
        finally:
            deadline.cancel()

    def close_connection_now(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            return

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        try:
            query = {key: values[0] for key, values in urllib.parse.parse_qs(url.query, max_num_fields=MAX_FIELDS).items()}
        except ValueError:
            return self.respond(400, "malformed query", "text/plain; charset=utf-8")
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
        if not form.get("csrf") or not hmac.compare_digest(form["csrf"], REQUEST.csrf):
            return self.respond(403, layout("Form expired", '<p>This form expired. <a href="/">Reload the page</a> and try again.</p>'))
        if self.path in MEDIA:
            return self.respond(*MEDIA[self.path](form))
        if self.path == "/logout":
            SESSIONS.pop(self.cookies().get(SESSION_COOKIE, ""), None)
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
        REQUEST.session = session_for(self.cookies().get(SESSION_COOKIE, "")) if AUTH["required"] else None
        if REQUEST.session:
            REQUEST.csrf, REQUEST.new_csrf = REQUEST.session["csrf"], False
            return
        REQUEST.csrf = self.cookies().get(CSRF_COOKIE) or secrets.token_urlsafe(24)
        REQUEST.new_csrf = CSRF_COOKIE not in self.cookies()

    def read_form(self):
        length = self.headers.get("Content-Length", "0")
        if not (length.isascii() and length.isdigit()) or int(length) > MAX_BODY_BYTES:
            return None
        body = self.rfile.read(int(length)).decode(errors="replace")
        try:
            return {key: values[0] for key, values in urllib.parse.parse_qs(body, max_num_fields=MAX_FIELDS).items()}
        except ValueError:
            return None

    def login(self, form):
        if not AUTH["required"]:
            return self.redirect("/")
        client = self.client_address[0]
        if locked_out(client):
            return self.respond(429, login_page(f"Too many wrong attempts. Wait {LOCKOUT_SECONDS // 60} minutes and try again."))
        user = form.get("user", "")
        if not verify(user, form.get("password", "")):
            record_failure(client)
            return self.respond(401, login_page("That user name or password is not right.", user))
        session = open_session(user)
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        log("web.login", f"user {user} logged in from {client}", user=user, client=client)
        self.redirect("/", [f"{SESSION_COOKIE}={session}; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict; Path=/{secure}"])

    def authorised(self):
        return not AUTH["required"] or REQUEST.session is not None

    def trusted_host(self):
        if AUTH["required"]:
            return True
        return host_name(self.headers.get("Host", "")) in LOOPBACK_HOSTS and not any(self.headers.get(name) for name in PROXY_HEADERS)

    def same_origin(self):
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if not origin:
            return True
        return urllib.parse.urlparse(origin).netloc == self.headers.get("Host", "")

    def cookies(self):
        return dict(part.strip().split("=", 1) for part in self.headers.get("Cookie", "").split(";") if "=" in part)

    def respond(self, status, body, content_type="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode()
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


def host_name(host):
    if host.startswith("["):
        return host[1:host.find("]")]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def login_page(problem="", user=""):
    warning = f'<p class="message" role="alert" id="login-problem">{escape(problem)}</p>' if problem else ""
    described = ' aria-invalid="true" aria-describedby="login-problem"' if problem else ""
    return layout("Log in", f'{warning}<form method="post" action="/login" class="login">'
                            f'<label for="user">User name</label><input type="text" id="user" name="user" value="{escape(user or DEFAULT_USER)}" autocomplete="username"{described}>'
                            f'<label for="password">Password</label><input type="password" id="password" name="password" autocomplete="current-password"{described}>'
                            '<button class="primary">Log in</button></form>'
                            '<p class="subtle words">Set the account with SUBTITLE_GUARD_UI_USER and SUBTITLE_GUARD_UI_PASSWORD, or with subtitle-guard set-password. '
                            'On first start without one, a password for admin is printed in the service logs.</p>', navigation=False)


def health_page():
    return 200, "ok", "text/plain; charset=utf-8"


def ready_page():
    try:
        jobs.counts()
    except sqlite3.Error as error:
        log("web.not-ready", f"database unavailable: {type(error).__name__}: {error}", LEVEL["error"])
        return 503, json.dumps({"ready": False, "database": "unavailable"}), "application/json"
    workers = worker_health()
    supervised = os.environ.get(SUPERVISED_VARIABLE) == "1"
    ready = all(workers.values()) and (bool(workers) or not supervised)
    return (200 if ready else 503), json.dumps({"ready": ready, "database": "ok", "workers": workers}), "application/json"


PAGES = {"/": dashboard_page, "/review": review_page, "/history": history_page, "/jobs": history_page, "/log": log_page, "/log.jsonl": raw_log,
         "/subtitle": subtitle_page, "/edit": editor_page, "/api/status": api_status, "/api/jobs": api_jobs, "/api/review": api_review, "/api/log": api_log}
MEDIA = {"/api/audio": api_audio, "/api/frame": api_frame}
