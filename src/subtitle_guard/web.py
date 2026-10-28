"""Web UI in plain words: what needs a look, what is out of sync and where, and one click to fix it."""

import hmac
import html
import json
import os
import re
import secrets
import threading
import time
import traceback
import urllib.parse
from collections import OrderedDict
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import jobs, review
from .library import SUBTITLE_EXTENSIONS
from .errors import SubtitleGuardError
from .logs import LEVEL, event_level, event_message, log, log_file, recent_events, settings
from .outcome import DECISION, DECISION_WORDS
from .reports import has_report, latest_reports, report_history

HOST = os.environ.get("SUBTITLE_GUARD_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("SUBTITLE_GUARD_UI_PORT", "8484"))
TOKEN = os.environ.get("SUBTITLE_GUARD_UI_TOKEN", "")
SESSION_COOKIE = "subtitle_guard_session"
CSRF_COOKIE = "subtitle_guard_csrf"
SESSION_SECONDS = 30 * 24 * 3600
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_BODY_BYTES = 64 * 1024
MAX_FIELDS = 50
MAX_CONNECTIONS = 32
MAX_NOTICES = 200
MAX_MINUTE_DIGITS = 5
REQUEST_TIMEOUT_SECONDS = 30
FAILED_LOGIN_LOG_SECONDS = 60
SECURITY_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; "
                                               "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}
ROOTS = [os.path.realpath(root) for root in os.environ.get("SUBTITLE_GUARD_ROOTS", os.environ.get("SUBTITLE_GUARD_LIBRARY", "")).split(os.pathsep) if root]
AUTH = {"token": TOKEN}
SESSIONS = set()
FAILED_LOGINS = {}
NOTICES = OrderedDict()
REQUEST = threading.local()
DIAGNOSES = threading.BoundedSemaphore(2)
WINDOW_SECONDS = 60
NOTICEABLE_SECONDS = 0.5
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%232f5fd0'/%3E"
           "%3Crect x='3' y='9' width='10' height='2' fill='white'/%3E%3Crect x='3' y='12' width='6' height='2' fill='white'/%3E%3C/svg%3E")
LOG_LIMIT = 300
HISTORY_LIMIT = 300
HISTORY_GROUPS = {"fixed": ("Fixed", ("fixed",)), "in-sync": ("In sync", ("kept",)), "not-fixed": ("Not fixed", ("rejected", "wrong-episode", "best-effort")),
                  "not-checked": ("Not checked", ("unverified", "audio-synced")), "by-hand": ("By hand", ("manual", "reverted"))}
DECISION_TONES = {"kept": "good", "fixed": "good", "manual": "good", "rejected": "bad", "wrong-episode": "bad", "best-effort": "warn",
                  "unverified": "neutral", "audio-synced": "warn", "reverted": "neutral"}
BAND_TONES = {"high": "good", "medium": "warn", "low": "bad", "none": "neutral"}
STARTED_BY = {"hook": "Bazarr", "scan": "Library scan", "check": "Check", "manual": "You, by hand", "ui": "You, Fix everything", "revert": "You, restore"}
KIND_WORDS = {"hook": "Check a subtitle from Bazarr", "check": "Check", "blacklist": "Ask Bazarr for another subtitle", "retry-check": "Wait for the other subtitle"}
CHANGES_SHOWN = 3
UNCHANGED_DECISIONS = ("kept", "rejected", "wrong-episode", "unverified")
SPEED_TOLERANCE = 0.001
TASK_STATES = {"running": ("neutral", "Working on it"), "queued": ("neutral", "Waiting"), "failed": ("bad", "Error")}
TICK_MINUTES = (1, 2, 5, 10, 15, 30, 60)
STYLE = """
:root { --background: #1a1c1e; --sidebar: #141517; --surface: #222528; --raised: #2a2e32; --text: #e4e5e7; --muted: #8d939a; --line: #33383d;
        --accent: #4aa3df; --good: #3fb871; --medium: #e2a93b; --bad: #e5534b; --reference: #262b31; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--background); color: var(--text); font: 15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.shell { display: grid; grid-template-columns: 300px minmax(0, 1fr); min-height: 100vh; }
aside { background: var(--sidebar); border-right: 1px solid var(--line); padding: 16px 0; position: sticky; top: 0; height: 100vh; overflow-y: auto; }
aside .brand { font-weight: 700; font-size: 16px; padding: 0 18px 14px; color: var(--text); display: block; }
aside nav a { display: block; padding: 7px 18px; color: var(--muted); }
aside nav a.active, aside nav a:hover { color: var(--text); background: var(--surface); text-decoration: none; }
aside h3 { font-size: 12px; color: var(--muted); font-weight: 600; margin: 18px 18px 6px; }
aside .item { display: grid; grid-template-columns: 10px minmax(0, 1fr); gap: 0 10px; align-items: center; padding: 8px 18px; border-left: 3px solid transparent; color: var(--text); }
aside .item:hover { background: var(--surface); text-decoration: none; }
aside .item.current { background: var(--raised); border-left-color: var(--accent); }
aside .item .name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
aside .item .why { grid-column: 2; color: var(--muted); font-size: 13px; }
aside .empty { padding: 4px 18px; color: var(--muted); }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.good { background: var(--good); } .dot.warn { background: var(--medium); } .dot.bad { background: var(--bad); } .dot.neutral { background: var(--muted); }
main { padding: 24px 32px; width: 100%; max-width: 1480px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; word-break: break-all; }
h2 { font-size: 16px; margin: 28px 0 10px; }
.subtle { color: var(--muted); font-size: 13px; margin: 0 0 16px; word-break: break-all; }
.subtle.words { word-break: normal; }
.status { border-radius: 10px; padding: 14px 18px; margin: 0 0 16px; border: 1px solid; }
.status b { display: block; font-size: 17px; }
.status span { color: var(--muted); }
.status.good { border-color: var(--good); background: rgba(63, 184, 113, 0.08); }
.status.warn { border-color: var(--medium); background: rgba(226, 169, 59, 0.08); }
.status.bad { border-color: var(--bad); background: rgba(229, 83, 75, 0.08); }
.status.neutral { border-color: var(--line); background: var(--surface); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; color: var(--text); }
.card:hover { border-color: var(--accent); text-decoration: none; }
.card b { display: block; font-size: 26px; font-weight: 650; }
.card span { color: var(--muted); font-size: 13px; }
table.parts td { vertical-align: middle; }
table.parts tr td:first-child { border-left: 4px solid var(--bad); color: var(--text); }
table.parts td.actions { text-align: right; white-space: nowrap; width: 1%; }
table.parts td.actions a { margin-right: 12px; }
.reading-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.minute-links a { margin-left: 16px; font-size: 14px; }
table.history tr.day td { background: var(--background); color: var(--text); font-weight: 650; padding-top: 16px; border-bottom: 1px solid var(--line); }
table.history { table-layout: fixed; }
table.history td { overflow-wrap: break-word; }
table.history td.actions { width: 1%; white-space: nowrap; text-align: right; }
.badge { display: inline-block; padding: 1px 9px; border-radius: 10px; font-size: 13px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
.badge.good { color: var(--good); border-color: var(--good); } .badge.warn { color: var(--medium); border-color: var(--medium); }
.badge.bad { color: var(--bad); border-color: var(--bad); }
.tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
.tab { padding: 5px 12px; border: 1px solid var(--line); border-radius: 16px; color: var(--muted); font-size: 14px; }
.tab:hover { text-decoration: none; color: var(--text); }
.tab.active { background: var(--raised); color: var(--text); border-color: var(--accent); }
.tab .count { margin-left: 6px; font-size: 12px; color: var(--muted); }
.technical-log { font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: var(--sidebar); border: 1px solid var(--line);
                 border-radius: 8px; padding: 8px 12px; overflow-x: auto; }
.technical-log details.line { background: none; border: 0; border-bottom: 1px solid var(--surface); border-radius: 0; padding: 0; margin: 0; }
.technical-log details.line[open] summary { margin-bottom: 0; }
.technical-log summary { white-space: pre-wrap; overflow-wrap: anywhere; padding: 4px 0; cursor: pointer; list-style: none; }
.technical-log summary::-webkit-details-marker { display: none; }
.technical-log summary:hover { background: var(--surface); }
.technical-log .when, .technical-log .source { color: var(--muted); }
.technical-log .event { color: var(--accent); font-weight: 600; }
.technical-log .metadata { position: relative; margin: 2px 0 8px 2ch; }
.technical-log .metadata pre { margin: 0; padding: 10px 12px; background: var(--background); border: 1px solid var(--line); border-radius: 6px; overflow-x: auto;
                               white-space: pre; color: var(--text); }
.technical-log .copy { position: absolute; top: 6px; right: 6px; }
.technical-log .debug .level { color: var(--muted); } .technical-log .warning .level { color: var(--medium); }
.technical-log .error .level, .technical-log .error .text { color: var(--bad); }
form.filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }
select { padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--background); color: var(--text); font: inherit; }
form.filters input[type=text] { flex: 1 1 320px; max-width: 560px; width: auto; }
table { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
th { color: var(--muted); font-weight: 600; font-size: 13px; background: var(--raised); }
td.time { font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--muted); width: 1%; }
td.state { white-space: nowrap; font-size: 13px; width: 1%; }
td.state.late { color: var(--bad); } td.state.fine { color: var(--good); }
table.reading td.video, table.reading td.yours { width: 40%; }
table.reading td.video { background: var(--reference); }
table.reading tr.late td.yours { color: #f3b4af; }
td.pickable[data-time] { cursor: pointer; } td.pickable[data-time]:hover { background: var(--raised); }
.minutes { display: flex; height: 34px; border-radius: 6px; overflow: hidden; margin: 4px 0 0; background: var(--line); }
.minutes a { flex: 1 1 0; min-width: 0; display: block; opacity: 0.85; }
.minutes a:hover { opacity: 1; filter: brightness(1.2); }
.minutes .good, .legend .good { background: var(--good); } .minutes .medium, .legend .medium { background: var(--medium); }
.minutes .bad, .legend .bad { background: var(--bad); } .minutes .fixed, .legend .fixed { background: var(--accent); } .minutes .quiet, .legend .quiet { background: var(--line); }
.minutes a.current { box-shadow: inset 0 0 0 2px var(--text); opacity: 1; min-width: 4px; }
.changes { background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--good); border-radius: 8px; padding: 10px 14px; }
.ticks { position: relative; height: 20px; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.ticks span { position: absolute; top: 3px; padding-left: 3px; border-left: 1px solid var(--line); white-space: nowrap; }
.ticks span.end { right: 0; border-left: 0; padding-left: 0; }
.legend { color: var(--muted); font-size: 13px; display: flex; gap: 16px; flex-wrap: wrap; margin: 0 0 8px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 8px; }
form.inline { display: inline; }
button { padding: 8px 14px; border-radius: 7px; border: 1px solid var(--line); background: var(--raised); color: var(--text); cursor: pointer; font: inherit; }
button:hover { border-color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: #0d1b26; font-weight: 600; }
button.small { padding: 4px 10px; font-size: 13px; }
details { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; margin: 14px 0; }
details summary { cursor: pointer; color: var(--muted); }
details[open] summary { margin-bottom: 10px; }
details label { margin-right: 14px; color: var(--muted); font-size: 13px; }
input[type=text] { width: 92px; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--background); color: var(--text); font: inherit; }
.hint { color: var(--muted); font-size: 13px; margin: 8px 0 0; }
.message { background: rgba(74, 163, 223, 0.12); border: 1px solid var(--accent); padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; }
.muted { color: var(--muted); }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
aside form.logout { padding: 12px 18px; }
.keys { color: var(--muted); font-size: 12px; margin-top: 28px; }
kbd { font: 11px ui-monospace, monospace; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px; padding: 0 4px; }
@media (min-width: 1900px) {
  body { font-size: 16px; }
  main { max-width: 1680px; padding: 32px 48px; }
}
@media (max-width: 1200px) {
  .shell { grid-template-columns: 240px minmax(0, 1fr); }
  main { padding: 20px 22px; }
}
@media (max-width: 860px) {
  .shell { grid-template-columns: minmax(0, 1fr); }
  .keys { display: none; }
  aside { position: sticky; top: 0; z-index: 2; height: auto; padding: 8px 6px; border-right: 0; border-bottom: 1px solid var(--line); overflow: visible; }
  aside .brand { padding: 0 8px 4px; }
  aside nav { display: flex; flex-wrap: wrap; gap: 2px; }
  aside nav a { padding: 6px 10px; border-radius: 6px; white-space: nowrap; }
  aside h3, aside .item, aside .empty { display: none; }
  main { padding: 16px; }
  h1 { font-size: 18px; }
  .status { padding: 12px 14px; }
  .toolbar button { width: 100%; }
  .toolbar form.inline { flex: 1 1 100%; }
  .ticks span:nth-child(even):not(.end) { display: none; }
  table.history, table.parts, table.review, table.history tbody, table.parts tbody, table.review tbody { display: block; width: 100%; border: 0; background: none; }
  table.history colgroup, table.history tr:first-child, table.review tr:first-child, table.reading tr:first-child { display: none; }
  table.history tr, table.parts tr, table.review tr { display: block; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; margin: 0 0 8px; padding: 8px 0; }
  table.history tr.day { background: none; border: 0; padding: 8px 0 0; margin: 0; }
  table.history td, table.parts td, table.review td { display: block; border: 0; padding: 3px 12px; width: auto !important; text-align: left !important; }
  table.parts tr td:first-child { border-left: 0; }
  table.reading tr { display: grid; grid-template-columns: 1fr auto; border-bottom: 1px solid var(--line); }
  table.reading td { border: 0; width: auto !important; }
  table.reading td.time { grid-row: 1; grid-column: 1; }
  table.reading td.state { grid-row: 1; grid-column: 2; text-align: right; }
  table.reading td.video, table.reading td.yours { grid-column: 1 / 3; padding-top: 2px; padding-bottom: 2px; }
  table.reading td.video::before { content: "In the video: "; color: var(--muted); font-size: 12px; }
  table.reading td.yours::before { content: "Your subtitle: "; color: var(--muted); font-size: 12px; }
  .reading-head .minute-links a { margin: 0 16px 0 0; }
  form.filters input[type=text] { width: 100%; flex: 1 1 200px; }
}
"""
KEYS = """
document.addEventListener('keydown', event => {
  if (event.target.matches('input, textarea') || event.metaKey || event.ctrlKey || event.altKey) return;
  const targets = {j: 'next-item', k: 'previous-item', ArrowRight: 'next-minute', ArrowLeft: 'previous-minute'};
  if (targets[event.key] && document.getElementById(targets[event.key])) { window.location = document.getElementById(targets[event.key]).href; }
  if (event.key === 'a' && document.getElementById('accept-form')) { document.getElementById('accept-form').submit(); }
});
"""
COPY = """
document.querySelectorAll('button.copy').forEach(button => button.addEventListener('click', () => {
  const text = button.nextElementSibling.textContent;
  const done = () => { button.textContent = 'Copied'; setTimeout(() => { button.textContent = 'Copy'; }, 1500); };
  const select = () => {
    const range = document.createRange();
    range.selectNodeContents(button.nextElementSibling);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand('copy');
    done();
  };
  if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done, select); return; }
  select();
}));
"""
PICKER = """
let picked = null;
const clock = value => {
  const seconds = (value % 60).toFixed(2).padStart(5, '0');
  const hours = Math.floor(value / 3600);
  return hours ? `${hours}:${String(Math.floor(value / 60) % 60).padStart(2, '0')}:${seconds}` : `${Math.floor(value / 60)}:${seconds}`;
};
document.querySelectorAll('td.pickable[data-time]').forEach(cell => cell.addEventListener('click', () => {
  const time = parseFloat(cell.dataset.time);
  document.getElementById('by-hand').open = true;
  if (cell.classList.contains('yours')) {
    picked = time;
    document.getElementById('first').value = clock(Math.max(0, time - 0.05));
    document.getElementById('hint').textContent = 'Now click the moment in the video it should match, on the left.';
    return;
  }
  if (picked === null) return;
  document.getElementById('offset').value = (time - picked).toFixed(2);
  document.getElementById('hint').textContent = 'Done. Set where the part ends, then click Move.';
}));
"""


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
    token = open(path).read().strip()
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
    except Exception as error:
        return 503, json.dumps({"ready": False, "database": f"{type(error).__name__}: {error}"}), "application/json"
    return 200, json.dumps({"ready": True, "database": "ok"}), "application/json"


def notice(message):
    key = secrets.token_urlsafe(9)
    NOTICES[key] = message
    while len(NOTICES) > MAX_NOTICES:
        NOTICES.popitem(last=False)
    return key


def cached(name, function):
    store = getattr(REQUEST, "cache", None)
    if store is None:
        return function()
    if name not in store:
        store[name] = function()
    return store[name]


def perform(form):
    action, path = form.get("action", ""), form.get("path", "")
    if action == "retry-job":
        job = form.get("job", "")
        message = "It will run again shortly." if job.isdigit() and jobs.requeue(int(job)) else "Only a finished task can be run again."
        return "/history?" + urllib.parse.urlencode({"notice": notice(message)})
    if not known_subtitle(path):
        return "/review?" + urllib.parse.urlencode({"notice": notice("Unknown subtitle, nothing was changed.")})
    video = review.video_for(path)
    if video is None:
        return subtitle_link(path, message="The video for this subtitle was not found, nothing was changed.")
    if action in ("fix-part", "shift"):
        try:
            first, last, shift = seconds(form.get("first", "")), seconds(form.get("last", "")), float(form.get("offset", "").replace(",", "."))
        except ValueError:
            return subtitle_link(path, form.get("minute", "0"), "Nothing was changed: the times must look like 2:03 or 1:02:03.50, and the move like -1.5.")
        review.shift_range(video, path, first - 0.05, last + 0.05, shift)
        message = f"Done. The lines from {short_clock(first)} to {short_clock(last)} now appear {abs(shift):.1f} s {'earlier' if shift < 0 else 'later'}."
    elif action == "fix-all":
        fixed = review.fix_everything(video, path)["decision"] == "fixed"
        message = "Done. The whole subtitle is back in sync." if fixed else "It could not be fixed safely, so nothing was changed."
    elif action == "revert":
        message = "Done. The original file is back." if review.revert(path) else "There is no original file to go back to."
    elif action == "accept":
        review.accept(path)
        message = "OK. This subtitle leaves the list. It comes back only if the file changes."
    elif action == "recheck":
        review.recheck(video, path)
        message = "It will be checked again in the background."
    elif action == "another":
        asked = review.ask_for_another(video, path)
        message = "Bazarr is looking for another subtitle. It will be checked when it arrives." if asked else "This subtitle did not come from Bazarr."
    elif action == "move":
        try:
            moved = review.move_to_episode(video, path, form.get("episode", ""))
        except SubtitleGuardError as error:
            return subtitle_link(path, message=f"Nothing was changed: {error}")
        if moved is None:
            message = "That episode was not found next to this one, nothing was changed."
        else:
            path, message = moved[0], f"Moved to {form['episode']}. It will be checked there."
    else:
        message = "Nothing was changed."
    if form.get("back") == "review":
        return "/review?" + urllib.parse.urlencode({"notice": notice(f"{os.path.basename(path)}: {message}")})
    return subtitle_link(path, None if action == "move" else form.get("minute", "0"), message)


def known_subtitle(path):
    if not path.lower().endswith(SUBTITLE_EXTENSIONS) or not os.path.isfile(path) or os.path.islink(path):
        return False
    if ROOTS and not any(os.path.realpath(path).startswith(root + os.sep) for root in ROOTS):
        return False
    return has_report(path) or bool(jobs.for_subtitle(path, limit=1))


def dashboard_page(query):
    rows = cached("latest", latest_reports)
    needing_a_look = {item["subtitle"] for item in cached("review", review.review_items)}
    settled = [row for row in rows if row["subtitle"] not in needing_a_look]
    waiting = sum(jobs.counts()["status"].get(status, 0) for status in ("queued", "running"))
    cards = [("need a look", len(needing_a_look), "/review"), ("fixed automatically", count(settled, "fixed"), "/history?result=fixed"),
             ("in sync", count(settled, "kept", "manual"), "/history?result=in-sync"),
             ("cannot be checked", count(settled, "unverified", "audio-synced"), "/history?result=not-checked"),
             ("waiting to be checked", waiting, "/history"), ("errors", jobs.counts()["status"].get("failed", 0), "/history")]
    body = '<div class="cards">' + "".join(f'<a class="card" href="{link}"><b>{number}</b><span>{escape(label)}</span></a>' for label, number, link in cards) + "</div>"
    body += tasks_now() + '<h2>Latest checks</h2>' + history_table(report_history(limit=10)) + '<p><a href="/history">See the whole history</a></p>'
    return 200, layout("Overview", body, refresh=15, page="dashboard"), "text/html; charset=utf-8"


def review_page(query):
    items = cached("review", review.review_items)
    if not items:
        return 200, layout("Needs a look", message_box(query) + '<div class="status good"><b>Nothing needs a look.</b><span>Subtitles show up here when the guard is not sure.'
                                                                  "</span></div>", page="review"), "text/html; charset=utf-8"
    rows = "".join(f'<tr><td><span class="dot {tone(item)}"></span></td><td><a href="{subtitle_link(item["subtitle"])}">'
                   f'{escape(os.path.basename(item["subtitle"]))}</a><div class="muted">{escape(where(item["subtitle"]))}</div></td>'
                   f'<td>{escape(reason(item))}</td><td>{quick_fix(item)}</td><td class="time">{escape(readable(item["time"]))}</td></tr>' for item in items)
    body = (message_box(query) + "<p class=\"subtle words\">The guard was not sure about these subtitles. Use the button when there is one, "
            "or open the subtitle to see what is wrong.</p>"
            + f'<table class="review"><tr><th></th><th>Subtitle</th><th>What happened</th><th></th><th>Checked</th></tr>{rows}</table>' + keys_help())
    return 200, layout("Needs a look", body, page="review"), "text/html; charset=utf-8"


def quick_fix(item):
    hidden = f'<input type="hidden" name="path" value="{escape(item["subtitle"])}"><input type="hidden" name="back" value="review">'
    if item["decision"] == "wrong-episode" and item["note"]:
        return (f'<form class="inline" method="post">{hidden}<input type="hidden" name="action" value="move">'
                f'<input type="hidden" name="episode" value="{escape(item["note"])}"><button class="small primary">Move it to {escape(item["note"])}</button></form>')
    video = review.video_for(item["subtitle"])
    diagnosis = review.known_diagnosis(video, item["subtitle"]) if video else None
    if not diagnosis or not diagnosis["fixable"]:
        return ""
    return f'<form class="inline" method="post">{hidden}<input type="hidden" name="action" value="fix-all"><button class="small primary">Fix it</button></form>'


def history_page(query):
    rows = report_history()
    group = query.get("result") if query.get("result") in HISTORY_GROUPS else None
    options = [(None, "All", len(rows))] + [(key, label, sum(row["decision"] in decisions for row in rows)) for key, (label, decisions) in HISTORY_GROUPS.items()]
    shown = [row for row in rows if not group or row["decision"] in HISTORY_GROUPS[group][1]][:HISTORY_LIMIT]
    body = (message_box(query) + tasks_now() + "<h2>Every check</h2><p class=\"subtle words\">Every time the guard checked a subtitle, newest first: "
            "what started it, what it decided, how sure it was, and how many lines matched the video before and after.</p>"
            + tab_links("/history", "result", group, options) + history_table(shown))
    return 200, layout("History", body, refresh=15, page="history"), "text/html; charset=utf-8"


def tasks_now():
    items = [item for status in ("running", "queued", "failed") for item in jobs.recent(status, limit=50)]
    if not items:
        return ""
    rows = "".join(task_row(item) for item in items)
    return f'<h2>Tasks right now</h2><table class="history">{columns(14, 30, 22, 22, 12)}<tr><th>Since</th><th>Subtitle</th><th>Task</th><th>Status</th><th></th></tr>{rows}</table>'


def task_row(item):
    tone_name, label = TASK_STATES[item["status"]]
    error = f'<div class="muted">{escape(item["error"])}</div>' if item["error"] else ""
    button = (f'<form class="inline" method="post"><input type="hidden" name="action" value="retry-job"><input type="hidden" name="job" value="{item["id"]}">'
              '<button class="small">Run again</button></form>') if item["status"] == "failed" else ""
    return (f'<tr><td class="time">{escape(readable(item["started"] or item["created"]))}</td><td>{subtitle_cell(item["arguments"].get("subtitle", ""))}</td>'
            f'<td>{escape(KIND_WORDS.get(item["kind"], item["kind"]))}</td><td><span class="badge {tone_name}">{label}</span>{error}</td><td class="actions">{button}</td></tr>')


def history_table(rows):
    if not rows:
        return '<p class="muted">Nothing yet.</p>'
    body, day = "", None
    for row in rows:
        if day_label(row["time"]) != day:
            day = day_label(row["time"])
            body += f'<tr class="day"><td colspan="5">{day}</td></tr>'
        body += history_row(row)
    return f'<table class="history">{columns(9, 23, 22, 28, 18)}<tr><th>Time</th><th>Subtitle</th><th>Result</th><th>What changed</th><th>Confidence</th></tr>{body}</table>'


def columns(*widths):
    return "<colgroup>" + "".join(f'<col style="width: {width}%">' for width in widths) + "</colgroup>"


def history_row(row):
    belongs_elsewhere = row["decision"] == "wrong-episode" and row["note"]
    words = f"Belongs to {row['note']}" if belongs_elsewhere else DECISION_WORDS.get(row["decision"], row["decision"])
    result = f'<span class="badge {DECISION_TONES.get(row["decision"], "neutral")}">{escape(words)}</span>'
    note = f'<div class="muted">{escape(row["note"])}</div>' if row["note"] and not belongs_elsewhere else ""
    return (f'<tr><td class="time">{escape(row["time"][11:19])}</td><td>{subtitle_cell(row["subtitle"])}</td>'
            f'<td>{result}{note}<div class="muted">Started by: {escape(started_by(row["provider"]))}</div></td><td>{changes_text(row["changes"], row["decision"])}</td>'
            f'<td>{confidence_badge(row)}{lines_matching(row)}</td></tr>')


def changes_text(changes, decision):
    if not changes and decision in UNCHANGED_DECISIONS:
        return '<span class="muted">Nothing moved</span>'
    if not changes:
        return '<span class="muted">Not recorded for older checks</span>'
    sentences = [f"Speed corrected: it was {speed_label(changes['scale'])}"] if speed_changed(changes["scale"]) else []
    for part in changes["parts"][:CHANGES_SHOWN]:
        lines = f"All {changes['lines']} lines" if part["lines"] >= 0.9 * changes["lines"] else (
            f"{part['lines']} lines from {short_clock(part['first'])} to {short_clock(part['last'])}")
        sentences.append(f"{lines} moved {abs(part['shift']):.1f} s {'earlier' if part['shift'] < 0 else 'later'}")
    if len(changes["parts"]) > CHANGES_SHOWN:
        sentences.append(f"and {len(changes['parts']) - CHANGES_SHOWN} more parts")
    return "".join(f"<div>{escape(sentence)}</div>" for sentence in sentences) or '<span class="muted">Small timing touch-ups</span>'


def speed_changed(scale):
    return abs(scale - 1) > SPEED_TOLERANCE


def speed_label(scale):
    return f"made for a {'faster' if scale > 1 else 'slower'} version of the video ({abs(scale - 1):.1%})"


def started_by(provider):
    if not provider:
        return "-"
    return STARTED_BY.get(provider, f"Bazarr, from {provider}")


def confidence_badge(row):
    if not row["confidence"]:
        return '<span class="muted">not recorded</span>'
    return f'<span class="badge {BAND_TONES.get(row["band"], "neutral")}">{escape(row["confidence"])} / 100, {escape(row["band"])}</span>'


def lines_matching(row):
    before, after = percent(row["matched_before"]), percent(row["matched"])
    if not after or not before or before == after:
        return ""
    return f'<div class="muted">lines matching the video: {before} before, {after} after</div>'


def percent(value):
    return f"{float(value):.0%}" if value else ""


def subtitle_cell(path):
    if not path:
        return ""
    name = escape(os.path.basename(path))
    link = f'<a href="{subtitle_link(path)}">{name}</a>' if os.path.isfile(path) else name
    return f'{link}<div class="muted">{escape(where(path))}</div>'


def log_page(query):
    search = (f'<form class="filters"><input type="text" name="q" placeholder="Search a file, an event, a word..." value="{escape(query.get("q", ""))}">'
              '<button>Search</button></form>')
    body = search + technical_log(filtered_events(recent_events(), query)[:LOG_LIMIT])
    return 200, layout("Log", body, refresh=None if query else 15, page="log"), "text/html; charset=utf-8"


def filtered_events(events, query):
    text = query.get("q", "").lower()
    return [event for event in events if not text or text in json.dumps(event, ensure_ascii=False).lower()]


def technical_log(events):
    if not events:
        return '<p class="muted">Nothing logged.</p>'
    lines = "".join(technical_line(event) for event in events)
    return (f'<p class="subtle words">Newest first. Click a line to open its metadata. <a href="/log.jsonl">Download the raw log</a>.</p>'
            f'<div class="technical-log">{lines}</div><script>{COPY}</script>')


def technical_line(event):
    level = event_level(event)
    metadata = escape(json.dumps(event, ensure_ascii=False, indent=2, default=str))
    return (f'<details class="line {escape(level)}"><summary><span class="when">{escape(event["time"])}</span> <span class="level">{escape(level.upper()):<7}</span> '
            f'<span class="source">{escape(event.get("source", "-")):<10}</span> <b class="event">{escape(event["event"])}</b> '
            f'<span class="text">{escape(event_message(event))}</span></summary>'
            f'<div class="metadata"><button class="small copy" type="button">Copy</button><pre>{metadata}</pre></div></details>')


def raw_log(query):
    return 200, open(log_file()).read() if os.path.exists(log_file()) else "", "text/plain; charset=utf-8"


def tab_links(base, key, current, options, keep=None):
    links = ""
    for value, label, number in options:
        query = {name: kept for name, kept in (keep or {}).items() if name != key}
        if value:
            query[key] = value
        href = base + ("?" + urllib.parse.urlencode(query) if query else "")
        badge = f'<span class="count">{number}</span>' if number is not None else ""
        links += f'<a class="tab{" active" if value == current else ""}" href="{href}">{escape(label)}{badge}</a>'
    return f'<nav class="tabs">{links}</nav>'


def day_label(moment):
    day = date.fromisoformat(moment[:10])
    if day == date.today():
        return "Today"
    if day == date.today() - timedelta(days=1):
        return "Yesterday"
    return day.strftime("%A %d %B %Y")


def subtitle_page(query):
    path = query.get("path", "")
    if not known_subtitle(path):
        return 404, layout("Unknown subtitle", "<p>This subtitle is not known to the guard.</p>"), "text/html; charset=utf-8"
    video = review.video_for(path)
    if video is None:
        return 200, layout(os.path.basename(path), '<div class="status neutral"><b>The video for this subtitle was not found.</b></div>', current=path), \
            "text/html; charset=utf-8"
    latest = next((row for row in cached("latest", latest_reports) if row["subtitle"] == path), None)
    with DIAGNOSES:
        diagnosis = review.diagnose(video, path)
    minute = int(query["minute"]) if query.get("minute", "").isdigit() and len(query["minute"]) <= MAX_MINUTE_DIGITS else first_problem_minute(diagnosis)
    view = review.side_by_side(video, path, minute * 60, minute * 60 + WINDOW_SECONDS, diagnosis["shifts"] if diagnosis["fixable"] else None)
    fix = review.last_fix(video, path)
    hidden = f'<input type="hidden" name="path" value="{escape(path)}"><input type="hidden" name="minute" value="{minute}">'
    body = (message_box(query) + f'<p class="subtle">{escape(where(path))}</p>' + status_box(diagnosis, latest, fix)
            + f'<div class="toolbar">{actions(video, path, diagnosis, latest, hidden)}</div>' + parts_list(path, diagnosis, hidden) + fix_section(path, fix)
            + timeline(path, view, minute, fix) + reading(path, view, diagnosis, minute, fix) + by_hand(hidden, minute)
            + technical(path, video, view, latest) + queue_links(path) + keys_help() + f"<script>{PICKER}</script>")
    return 200, layout(os.path.basename(path), body, current=path), "text/html; charset=utf-8"


def status_box(diagnosis, latest, fix):
    parts = len(diagnosis["parts"])
    if latest and latest["decision"] == "wrong-episode" and latest["note"]:
        return f'<div class="status bad"><b>This subtitle belongs to {escape(latest["note"])}.</b><span>Its lines match that episode, not this one.</span></div>'
    messages = {
        "in-sync": ("good", "This subtitle is in sync with the video.", "Nothing to do."),
        "unclear": ("warn", "This subtitle looks in sync, but a few minutes are unclear.", "Check the minutes in orange or red below."),
        "out-of-sync": ("bad", f"{parts} part{'s' if parts != 1 else ''} of this subtitle {'are' if parts != 1 else 'is'} out of sync.",
                        "Fix everything at once, or one part at a time."),
        "no-match": ("bad", "This subtitle does not match this video.", "It may be made for another version. Try another subtitle, or fix it by hand."),
        "no-reference": ("neutral", "This subtitle cannot be checked.", "The video has no subtitle track to compare with."),
        "too-short": ("neutral", "This subtitle cannot be checked.", "It has too few lines, which is normal for forced subtitles."),
    }
    tone_name, title, detail = messages[diagnosis["state"]]
    if diagnosis["state"] == "in-sync" and fix:
        detail = f"It was fixed on {fix_time(fix['time'])}. What was changed is shown below."
    if diagnosis["state"] == "out-of-sync" and not parts:
        title, detail = "Some lines are slightly off.", "Fix everything to put them back in place."
    if diagnosis["state"] == "out-of-sync" and speed_changed(diagnosis["scale"]):
        drift = "earlier and earlier" if diagnosis["scale"] > 1 else "later and later"
        title, detail = "This subtitle runs at the wrong speed.", f"It was {speed_label(diagnosis['scale'])}, so its lines drift {drift}. Fix everything puts them back."
    return f'<div class="status {tone_name}"><b>{escape(title)}</b><span>{escape(detail)}</span></div>'


def first_problem_minute(diagnosis):
    if diagnosis["parts"]:
        return int(diagnosis["parts"][0]["first"] // 60)
    return diagnosis["weak_minutes"][0] if diagnosis["weak_minutes"] else 0


def actions(video, path, diagnosis, latest, hidden):
    buttons = []
    if latest and latest["decision"] == "wrong-episode" and latest["note"]:
        buttons.append(("move", f"Move it to {latest['note']}", True, f'<input type="hidden" name="episode" value="{escape(latest["note"])}">'))
    if diagnosis["fixable"]:
        buttons.append(("fix-all", "Fix everything", not buttons, ""))
    buttons.append(("accept", "It looks fine, keep it", False, ""))
    buttons.append(("revert", "Restore the original file", False, ""))
    if review.came_from_bazarr(video, path):
        buttons.append(("another", "Try another subtitle", False, ""))
    return "".join(f'<form class="inline" method="post" id="{action}-form">{hidden}{extra}<input type="hidden" name="action" value="{action}">'
                   f'<button class="{"primary" if primary else ""}">{escape(label)}</button></form>' for action, label, primary, extra in buttons)


def parts_list(path, diagnosis, hidden):
    if not diagnosis["parts"] or speed_changed(diagnosis["scale"]):
        return ""
    rows = ""
    for part in diagnosis["parts"]:
        direction = "too late" if part["shift"] < 0 else "too early"
        fields = (f'<input type="hidden" name="first" value="{part["first"]:.3f}"><input type="hidden" name="last" value="{part["last"]:.3f}">'
                  f'<input type="hidden" name="offset" value="{part["shift"]:.2f}">')
        rows += (f'<tr><td class="time">{short_clock(part["first"])} to {short_clock(part["last"])}</td>'
                 f'<td>{part["lines"]} lines appear <b>{abs(part["shift"]):.1f} s {direction}</b></td>'
                 f'<td class="actions"><a href="{subtitle_link(path, int(part["first"] // 60))}">Show</a>'
                 f'<form class="inline" method="post">{hidden}{fields}<input type="hidden" name="action" value="fix-part"><button class="small">Fix this part</button></form></td></tr>')
    count = len(diagnosis["parts"])
    return f'<h2>What is wrong ({count} part{"s" if count != 1 else ""})</h2><table class="parts">{rows}</table>'


def timeline(path, view, minute, fix):
    if not view["minutes"]:
        return ""
    fixed = set(fix["moved_minutes"]) if fix else set()
    minutes = [(number, share, "fixed" if state == "good" and number in fixed else state) for number, share, state in view["minutes"]]
    legend = ('<p class="legend"><span><i class="good"></i>on time</span>' + ('<span><i class="fixed"></i>fixed by the guard, now on time</span>' if fixed else "")
              + '<span><i class="medium"></i>some lines off</span><span><i class="bad"></i>most lines off</span><span><i class="quiet"></i>almost no lines</span>'
              '<span>Click anywhere to read that minute.</span></p>')
    return f'<h2>The whole video ({duration_label(len(minutes))})</h2>{legend}{strip(path, minutes, minute)}'


def strip(path, minutes, minute):
    length = len(minutes)
    segments = "".join(f'<a class="{state}{" current" if number == minute else ""}" title="{escape(minute_title(number, share))}" href="{subtitle_link(path, number)}"></a>'
                       for number, share, state in minutes)
    step = next((step for step in TICK_MINUTES if length / step <= 10), TICK_MINUTES[-1])
    ticks = "".join(f'<span style="left: {100 * number / length:.2f}%">{duration_label(number)}</span>' for number in range(0, length - step // 2, step))
    return f'<div class="minutes">{segments}</div><div class="ticks">{ticks}<span class="end">{duration_label(length)}</span></div>'


def fix_section(path, fix):
    if not fix:
        return ""
    recorded = next((row for row in report_history(path, limit=50) if row["changes"]), None)
    changes = recorded["changes"] if recorded else {"scale": 1.0, "lines": len(fix["shifts"]), "parts": fix["parts"]}
    by = f", started by {started_by(recorded['provider'])}, confidence {recorded['confidence']} / 100" if recorded and recorded["confidence"] else ""
    return (f'<h2>What was changed</h2><p class="subtle words">Compared with the file as it was before, on {escape(fix_time(fix["time"]))}{escape(by)}. '
            'The original is kept: Restore the original file puts it back.</p>'
            f'<div class="changes">{changes_text(changes, DECISION["fixed"])}</div>')


def fix_time(folder):
    return f"{folder[:4]}-{folder[4:6]}-{folder[6:8]} at {folder[9:11]}:{folder[11:13]}"


def minute_title(number, share):
    moment = f"{short_clock(number * 60)} to {short_clock(number * 60 + 60)}"
    return f"{moment}: almost no lines" if share is None else f"{moment}: {share:.0%} of lines on time"


def reading(path, view, diagnosis, minute, fix):
    rows = "".join(moment_row(moment, diagnosis, fix) for moment in view["moments"])
    if not rows:
        return ""
    left = "what the subtitles built into the video say" if view["reference"] else "nothing to compare with"
    return (f'<div class="reading-head"><h2>{short_clock(minute * 60)} to {short_clock(minute * 60 + 60)}, line by line</h2>{minute_links(path, view, minute)}</div>'
            f'<p class="subtle words">Each row is one moment of the video. On the left, {left}. '
            "On the right, the line of your subtitle that goes with it, and whether it shows on time.</p>"
            f'<table class="reading"><tr><th>Time</th><th>In the video</th><th>Your subtitle</th><th>On time?</th></tr>{rows}</table>')


def minute_links(path, view, minute):
    known = [number for number, _, state in view["minutes"] if state != "quiet"]
    earlier, later = [number for number in known if number < minute], [number for number in known if number > minute]
    links = f'<a id="previous-minute" href="{subtitle_link(path, earlier[-1])}">Previous minute</a>' if earlier else ""
    links += f'<a id="next-minute" href="{subtitle_link(path, later[0])}">Next minute</a>' if later else ""
    return f'<span class="minute-links">{links}</span>'


def moment_row(moment, diagnosis, fix):
    video_cell = escape(moment["video"]) if moment["video"] is not None else '<span class="muted">(no line here)</span>'
    subtitle_cell = escape(moment["subtitle"]) if moment["subtitle"] is not None else '<span class="muted">(no line here)</span>'
    state, label = "", ""
    if moment["subtitle"] is not None and abs(moment["shift"]) >= NOTICEABLE_SECONDS:
        state = "late"
        label = f"{abs(moment['shift']):.1f} s {'too late' if moment['shift'] < 0 else 'too early'}<div class=\"muted\">shows at {short_clock(moment['shown_at'])}</div>"
    elif moment["subtitle"] is not None and moment["video"] is not None and diagnosis["state"] in ("in-sync", "unclear", "out-of-sync"):
        state, label = "fine", "on time"
    moved = fix["shifts"][moment["index"]] if fix and moment.get("index") is not None and moment["index"] < len(fix["shifts"]) else 0.0
    if abs(moved) >= NOTICEABLE_SECONDS:
        label += f'<div class="muted">was at {short_clock(moment["shown_at"] - moved)}, moved {abs(moved):.1f} s {"later" if moved > 0 else "earlier"}</div>'
    video_time = f' data-time="{moment["time"]:.3f}"' if moment["video"] is not None else ""
    subtitle_time = f' data-time="{moment["shown_at"]:.3f}"' if moment["subtitle"] is not None else ""
    return (f'<tr class="{state}"><td class="time">{short_clock(moment["time"])}</td><td class="video pickable"{video_time}>{video_cell}</td>'
            f'<td class="yours pickable"{subtitle_time}>{subtitle_cell}</td><td class="state {state}">{label}</td></tr>')


def by_hand(hidden, minute):
    return (f'<details id="by-hand"><summary>Fix a part by hand</summary><form method="post">{hidden}<input type="hidden" name="action" value="shift">'
            f'<label>From <input type="text" id="first" name="first" value="{clock(minute * 60)}"></label>'
            f'<label>To <input type="text" id="last" name="last" value="{clock(minute * 60 + WINDOW_SECONDS)}"></label>'
            f'<label>Move by (s) <input type="text" id="offset" name="offset" value="0"></label><button class="primary">Move</button></form>'
            '<p class="hint" id="hint">Click a line of your subtitle on the right, then the moment in the video it should match on the left. The move is filled in for you.</p></details>')


def technical(path, video, view, latest):
    measured = view.get("measure") or {}
    facts = [("last check", f"{latest['decision']} ({latest['confidence'] or '-'} {latest['band']}) at {readable(latest['time'])}" if latest else "never"),
             ("reference track", view["reference"] or "none"), ("lines in sync now", measured.get("matched", "-")),
             ("worst 2 minutes", measured.get("worst_window", "-")), ("confidence now", f"{measured.get('confidence', '-')} {measured.get('confidence_band', '')}"),
             ("video", video)]
    rows = "".join(f'<tr><td class="time">{escape(label)}</td><td>{escape(value)}</td></tr>' for label, value in facts)
    history = history_table(report_history(path, limit=20))
    return f'<details><summary>Technical details</summary><table>{rows}</table><h2>Checks of this subtitle</h2>{history}</details>'


def queue_links(path):
    links = ""
    queue = [item["subtitle"] for item in cached("review", review.review_items)]
    if path in queue and queue.index(path) > 0:
        links += f'<a id="previous-item" href="{subtitle_link(queue[queue.index(path) - 1])}" hidden></a>'
    if path in queue and queue.index(path) + 1 < len(queue):
        links += f'<a id="next-item" href="{subtitle_link(queue[queue.index(path) + 1])}" hidden></a>'
    return links


def api_status(query):
    return 200, json.dumps({**jobs.counts(), "to_review": len(cached("review", review.review_items))}), "application/json"


def api_jobs(query):
    limit = int(query["limit"]) if query.get("limit", "").isdigit() and len(query["limit"]) <= 4 else 100
    return 200, json.dumps(jobs.recent(query.get("status"), limit=max(1, min(limit, 1000)))), "application/json"


def api_log(query):
    return 200, json.dumps(filtered_events(recent_events(), query)[:LOG_LIMIT]), "application/json"


def api_review(query):
    return 200, json.dumps(cached("review", review.review_items)), "application/json"


def layout(title, body, refresh=None, page="", current=None, navigation=True):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    links = "".join(f'<a class="{"active" if page == key else ""}" href="{link}">{label}</a>'
                    for key, link, label in (("dashboard", "/", "Overview"), ("review", "/review", "Needs a look"), ("history", "/history", "History"),
                                                    ("log", "/log", "Log")))
    logout = ('<form method="post" action="/logout" class="logout"><button class="small">Log out</button></form>' if AUTH["token"] and navigation else "")
    page_html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{meta}'
                 f'<title>{escape(title)} - subtitle-guard</title><link rel="icon" href="{FAVICON}"><style>{STYLE}</style></head><body><div class="shell">'
                 f'<aside><a class="brand" href="/">subtitle-guard</a>{f"<nav>{links}</nav>{sidebar(current)}{logout}" if navigation else ""}</aside>'
                 f'<main><h1>{escape(title)}</h1>{body}</main></div><script>{KEYS}</script></body></html>')
    csrf = escape(getattr(REQUEST, "csrf", ""))
    return re.sub(r'(<form\b[^>]*method="post"[^>]*>)', lambda match: match.group(1) + f'<input type="hidden" name="csrf" value="{csrf}">', page_html)


def sidebar(current):
    items = cached("review", review.review_items)
    if not items:
        return '<h3>Needs a look</h3><p class="empty">Nothing right now.</p>'
    entries = "".join(f'<a class="item{" current" if item["subtitle"] == current else ""}" title="{escape(item["subtitle"])}" '
                      f'href="{subtitle_link(item["subtitle"])}"><span class="dot {tone(item)}"></span>'
                      f'<span class="name">{escape(os.path.basename(item["subtitle"]))}</span><span class="why">{escape(reason(item))}</span></a>' for item in items)
    return f"<h3>Needs a look ({len(items)})</h3>{entries}"


def reason(item):
    if item["decision"] == "wrong-episode" and item["note"]:
        return f"Belongs to {item['note']}"
    if item["weak_minutes"]:
        return f"Out of sync around {short_clock(int(item['weak_minutes'].split(',')[0]) * 60)}"
    if item["decision"] == "fixed" and item["band"] == "low":
        return "Fixed, but not sure"
    if item["decision"] == "rejected":
        return "Does not match the video"
    return DECISION_WORDS.get(item["decision"], item["decision"])


def tone(item):
    if item["decision"] in ("rejected", "wrong-episode") or item["band"] == "low":
        return "bad"
    if item["decision"] in ("unverified", "audio-synced", "reverted"):
        return "neutral"
    return "warn"


def where(path):
    folder = os.path.dirname(path)
    return " / ".join(part for part in (os.path.basename(os.path.dirname(folder)), os.path.basename(folder)) if part)


def count(rows, *decisions):
    return sum(row["decision"] in decisions for row in rows)


def subtitle_link(path, minute=None, message=None):
    query = {"path": path} if minute is None else {"path": path, "minute": minute}
    if message:
        query["notice"] = notice(message)
    return "/subtitle?" + urllib.parse.urlencode(query)


def keys_help():
    return '<p class="keys">Keyboard: <kbd>j</kbd> <kbd>k</kbd> next or previous subtitle, <kbd>Left</kbd> <kbd>Right</kbd> previous or next minute, <kbd>a</kbd> keep it.</p>'


def message_box(query):
    message = NOTICES.pop(query.get("notice", ""), "")
    return f'<div class="message" role="status">{escape(message)}</div>' if message else ""


def readable(moment):
    return moment.replace("T", " ")


def clock(value):
    hours, rest = divmod(value, 3600)
    return f"{int(hours)}:{int(rest // 60):02d}:{rest % 60:05.2f}" if hours >= 1 else f"{int(rest // 60)}:{rest % 60:05.2f}"


def short_clock(value):
    hours, rest = divmod(int(value), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def duration_label(minutes):
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours}h{rest:02d}" if rest else f"{hours}h"


def seconds(value):
    *larger, whole = value.strip().replace(",", ".").split(":")
    return float(whole) + sum(int(part) * unit for part, unit in zip(reversed(larger), (60, 3600)))


def escape(value):
    return html.escape(str(value), quote=True)


PAGES = {"/": dashboard_page, "/review": review_page, "/history": history_page, "/jobs": history_page, "/log": log_page, "/log.jsonl": raw_log,
         "/subtitle": subtitle_page, "/api/status": api_status, "/api/jobs": api_jobs, "/api/review": api_review, "/api/log": api_log}
