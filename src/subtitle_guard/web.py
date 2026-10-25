"""Web UI: dashboard, review queue, side-by-side reading with manual adjustment, and job tracking."""

import html
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import jobs, review
from .library import SUBTITLE_EXTENSIONS
from .storage import latest_reports

HOST = os.environ.get("SUBTITLE_GUARD_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("SUBTITLE_GUARD_UI_PORT", "8484"))
TOKEN = os.environ.get("SUBTITLE_GUARD_UI_TOKEN", "")
TOKEN_COOKIE = "subtitle_guard_token"
WINDOW_SECONDS = 60
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%232f5fd0'/%3E"
           "%3Crect x='3' y='9' width='10' height='2' fill='white'/%3E%3Crect x='3' y='12' width='6' height='2' fill='white'/%3E%3C/svg%3E")
STYLE = """
:root { --background: #f7f7f5; --surface: #ffffff; --text: #1d1d1b; --muted: #6b6b66; --line: #e3e2dd; --accent: #2f5fd0;
        --good: #1f7a45; --medium: #9a6a00; --bad: #b3261e; --good-soft: #e3f3e8; --bad-soft: #fbe6e4; --reference: #eef1fb; }
@media (prefers-color-scheme: dark) {
  :root { --background: #151514; --surface: #1e1e1c; --text: #ecebe6; --muted: #a3a29b; --line: #34332f; --accent: #8aa8ff;
          --good: #6fcf97; --medium: #e5b84d; --bad: #ff8a80; --good-soft: #1d3326; --bad-soft: #3a2220; --reference: #222838; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--background); color: var(--text); font: 15px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; }
header { background: var(--surface); border-bottom: 1px solid var(--line); padding: 12px 20px; display: flex; gap: 20px; align-items: baseline; flex-wrap: wrap; }
header strong { font-size: 17px; }
header a { color: var(--muted); text-decoration: none; }
header a:hover, a { color: var(--accent); }
main { max-width: 1200px; margin: 0 auto; padding: 20px; }
h1 { font-size: 20px; margin: 0 0 16px; word-break: break-all; }
h2 { font-size: 16px; margin: 24px 0 10px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; min-width: 120px; color: var(--text); text-decoration: none; }
.card:hover { border-color: var(--accent); }
.card b { display: block; font-size: 22px; }
.card span { color: var(--muted); font-size: 13px; }
table { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 13px; }
td.time { font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--muted); width: 1%; }
table.reading td:nth-child(2), table.reading td:nth-child(3) { width: 49%; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; border: 1px solid currentColor; }
.high, .match { color: var(--good); } .medium { color: var(--medium); } .low, .none, .off, .failed { color: var(--bad); }
tr.reference td { background: var(--reference); }
tr.subtitle.off td { background: var(--bad-soft); }
tr.subtitle.match td { background: var(--good-soft); }
tr.pickable { cursor: pointer; }
.minutes { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 14px; }
.minutes a { display: block; min-width: 34px; padding: 3px 4px; text-align: center; border-radius: 4px; font-size: 12px; text-decoration: none; color: #fff; }
.minutes a.current { outline: 2px solid var(--text); }
form.inline { display: inline; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin: 14px 0; }
.panel label { margin-right: 12px; color: var(--muted); font-size: 13px; }
input[type=text] { width: 90px; padding: 5px 7px; border: 1px solid var(--line); border-radius: 6px; background: var(--background); color: var(--text); }
button { padding: 6px 12px; border-radius: 6px; border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer; margin: 2px 4px 2px 0; }
button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.message { background: var(--good-soft); border: 1px solid var(--good); padding: 8px 12px; border-radius: 6px; margin-bottom: 14px; }
.muted { color: var(--muted); }
@media (max-width: 700px) { main { padding: 12px; } th:nth-child(n+5), td:nth-child(n+5) { display: none; } }
"""
PICKER = """
let picked = null;
const clock = value => `${Math.floor(value / 60)}:${(value % 60).toFixed(2).padStart(5, '0')}`;
document.querySelectorAll('tr.pickable').forEach(row => row.addEventListener('click', () => {
  const time = parseFloat(row.dataset.time);
  if (row.classList.contains('subtitle')) {
    picked = time;
    document.getElementById('first').value = clock(Math.max(0, time - 0.05));
    document.getElementById('hint').textContent = 'Now click the reference line this subtitle line belongs to.';
    return;
  }
  if (picked === null) return;
  document.getElementById('offset').value = (time - picked).toFixed(2);
  document.getElementById('hint').textContent = 'Offset filled in. Set the end of the stretch, then apply.';
}));
"""


def serve_ui(host=HOST, port=PORT):
    ThreadingHTTPServer((host, port), Handler).serve_forever()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(url.query).items()}
        if url.path == "/health":
            return self.respond(200, "ok", "text/plain")
        if not self.authorised(query):
            return self.respond(401, layout("Token needed", '<form><input type="text" name="token" placeholder="token"> <button>Open</button></form>'))
        if TOKEN and query.get("token") == TOKEN:
            return self.redirect(url.path, cookie=True)
        pages = {"/": dashboard_page, "/review": review_page, "/jobs": jobs_page, "/subtitle": subtitle_page,
                 "/api/status": api_status, "/api/jobs": api_jobs, "/api/review": api_review}
        if url.path not in pages:
            return self.respond(404, layout("Not found", "<p>Nothing here.</p>"))
        status, body, content_type = pages[url.path](query)
        self.respond(status, body, content_type)

    def do_POST(self):
        if not self.authorised({}):
            return self.respond(401, layout("Token needed", "<p>Open the UI with its token first.</p>"))
        length = int(self.headers.get("Content-Length", 0))
        form = {key: values[0] for key, values in urllib.parse.parse_qs(self.rfile.read(length).decode()).items()}
        self.redirect(perform(form))

    def authorised(self, query):
        if not TOKEN:
            return True
        cookies = dict(part.strip().split("=", 1) for part in self.headers.get("Cookie", "").split(";") if "=" in part)
        return query.get("token") == TOKEN or cookies.get(TOKEN_COOKIE) == TOKEN

    def respond(self, status, body, content_type="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, cookie=False):
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", f"{TOKEN_COOKIE}={TOKEN}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()

    def log_message(self, format, *arguments):
        return


def perform(form):
    action, path = form.get("action", ""), form.get("path", "")
    if action == "retry-job":
        message = "Job queued again." if jobs.requeue(int(form.get("job", "0"))) else "Only a finished or failed job can be retried."
        return "/jobs?" + urllib.parse.urlencode({"message": message})
    if not known_subtitle(path):
        return "/review?" + urllib.parse.urlencode({"message": "Unknown subtitle, nothing done."})
    video = review.video_for(path)
    if action == "shift" and video:
        outcome = review.shift_range(video, path, seconds(form["first"]), seconds(form["last"]), float(form["offset"]))
        message = f"Shifted. Confidence now {outcome['confidence']} ({outcome['confidence_band']})."
    elif action == "revert":
        message = "Restored the original file." if review.revert(path) else "No original backup found."
    elif action == "accept":
        review.accept(path)
        message = "Accepted. It leaves the review list until the file changes."
    elif action == "recheck" and video:
        message = f"Queued check job {review.recheck(video, path)}. A worker must be running."
    elif action == "another" and video:
        message = "Asked Bazarr for another subtitle." if review.ask_for_another(video, path) else "No Bazarr download recorded for this subtitle."
    else:
        message = "Nothing done: the video for this subtitle was not found."
    return "/subtitle?" + urllib.parse.urlencode({"path": path, "minute": form.get("minute", "0"), "message": message})


def known_subtitle(path):
    if not path.lower().endswith(SUBTITLE_EXTENSIONS) or not os.path.isfile(path):
        return False
    return any(row["subtitle"] == path for row in latest_reports()) or bool(jobs.for_subtitle(path, limit=1))


def dashboard_page(query):
    counts = jobs.counts()
    items = review.review_items()
    cards = [("to review", len(items), "/review")] + [(f"jobs {status}", number, f"/jobs?status={status}") for status, number in sorted(counts["status"].items())]
    cards += [(f"confidence {band}", number, "/jobs") for band, number in sorted(counts["confidence"].items())]
    body = '<div class="cards">' + "".join(f'<a class="card" href="{link}"><b>{number}</b><span>{escape(label)}</span></a>' for label, number, link in cards) + "</div>"
    body += "<h2>Latest jobs</h2>" + jobs_table(jobs.recent(limit=25))
    return 200, layout("Dashboard", body, refresh=15), "text/html; charset=utf-8"


def review_page(query):
    rows = "".join(f'<tr><td><a href="/subtitle?{urllib.parse.urlencode({"path": item["subtitle"], "minute": first_weak_minute(item)})}">'
                   f'{escape(os.path.basename(item["subtitle"]))}</a><div class="muted">{escape(os.path.dirname(item["subtitle"]))}</div></td>'
                   f'<td>{escape(item["decision"])}</td><td>{band_badge(item["band"], item["confidence"])}</td>'
                   f'<td>{escape(item["weak_minutes"] or "-")}</td><td>{escape(item["note"])}</td><td class="time">{escape(readable(item["time"]))}</td></tr>'
                   for item in review.review_items())
    table = ("<table><tr><th>Subtitle</th><th>Decision</th><th>Confidence</th><th>Weak minutes</th><th>Note</th><th>Checked</th></tr>" + rows + "</table>"
             if rows else '<p class="muted">Nothing to review.</p>')
    return 200, layout("To review", message_box(query) + table), "text/html; charset=utf-8"


def jobs_page(query):
    status = query.get("status")
    filters = " ".join(f'<a href="/jobs?status={name}">{name}</a>' for name in jobs.STATUS)
    body = message_box(query) + f'<p class="muted">Filter: <a href="/jobs">all</a> {filters}</p>' + jobs_table(jobs.recent(status, limit=200), with_retry=True)
    return 200, layout("Jobs", body, refresh=15), "text/html; charset=utf-8"


def subtitle_page(query):
    path = query.get("path", "")
    if not known_subtitle(path):
        return 404, layout("Unknown subtitle", "<p>This subtitle is not known to the guard.</p>"), "text/html; charset=utf-8"
    video = review.video_for(path)
    minute = int(query.get("minute", "0") or 0)
    view = review.side_by_side(video, path, minute * 60, minute * 60 + WINDOW_SECONDS) if video else {"lines": [], "minutes": [], "reference": None}
    measured = view.get("measure") or {}
    summary = (f'<p>Reference <b>{escape(view["reference"] or "none")}</b> | matched {measured.get("matched", "-")} | worst 2-min window {measured.get("worst_window", "-")} | '
               f'confidence {band_badge(measured.get("confidence_band", "none"), measured.get("confidence", ""))} | weak minutes '
               f'{escape(", ".join(str(value) for value in measured.get("weak_minutes", [])) or "-")}</p>') if measured else '<p class="muted">No embedded reference for this video.</p>'
    minutes = "".join(f'<a class="{"current" if number == minute else ""}" style="background:{share_colour(share)}" title="{share:.0%} of lines match" '
                      f'href="/subtitle?{urllib.parse.urlencode({"path": path, "minute": number})}">{number}</a>' for number, share in view["minutes"])
    rows = "".join(f'<tr class="{side} pickable {"" if matched is None else ("match" if matched else "off")}" data-time="{start:.3f}">'
                   f'<td class="time">{clock(start)}</td><td>{escape(text) if side == "reference" else ""}</td>'
                   f'<td>{escape(text) if side == "subtitle" else ""}</td></tr>' for start, side, text, matched in view["lines"])
    hidden = f'<input type="hidden" name="path" value="{escape(path)}"><input type="hidden" name="minute" value="{minute}">'
    actions = "".join(f'<form class="inline" method="post">{hidden}<input type="hidden" name="action" value="{action}"><button>{label}</button></form>'
                      for action, label in (("accept", "Accept as is"), ("recheck", "Check again"), ("revert", "Restore the original"), ("another", "Ask Bazarr for another")))
    shift = (f'<div class="panel"><form method="post">{hidden}<input type="hidden" name="action" value="shift">'
             f'<label>From <input type="text" id="first" name="first" value="{clock(minute * 60)}"></label>'
             f'<label>To <input type="text" id="last" name="last" value="{clock(minute * 60 + WINDOW_SECONDS)}"></label>'
             f'<label>Shift by (s) <input type="text" id="offset" name="offset" value="0"></label><button class="primary">Apply shift</button></form>'
             f'<p class="muted" id="hint">Click a subtitle line, then the reference line it belongs to, to fill in the shift.</p></div>')
    body = (message_box(query) + f'<p class="muted">{escape(video or "video not found")}</p>' + summary + f'<div class="minutes">{minutes}</div>'
            + f'<div class="panel">{actions}</div>' + shift
            + f"<h2>Minute {minute}</h2><table class=\"reading\"><tr><th>Time</th><th>Reference (embedded)</th><th>Subtitle</th></tr>{rows}</table>"
            + "<h2>History</h2>" + jobs_table(jobs.for_subtitle(path)) + f"<script>{PICKER}</script>")
    return 200, layout(os.path.basename(path), body), "text/html; charset=utf-8"


def api_status(query):
    return 200, json.dumps({**jobs.counts(), "to_review": len(review.review_items())}), "application/json"


def api_jobs(query):
    return 200, json.dumps(jobs.recent(query.get("status"), limit=int(query.get("limit", "100")))), "application/json"


def api_review(query):
    return 200, json.dumps(review.review_items()), "application/json"


def jobs_table(items, with_retry=False):
    if not items:
        return '<p class="muted">No jobs yet.</p>'
    rows = ""
    for item in items:
        subtitle = item["arguments"].get("subtitle", "")
        link = f'<a href="/subtitle?{urllib.parse.urlencode({"path": subtitle})}">{escape(os.path.basename(subtitle))}</a>' if subtitle else ""
        retry = (f'<form class="inline" method="post"><input type="hidden" name="action" value="retry-job"><input type="hidden" name="job" value="{item["id"]}">'
                 f'<button>Retry</button></form>') if with_retry and item["status"] in ("done", "failed") else ""
        detail = escape(item["error"] or item["summary"] or "")
        rows += (f'<tr><td>{item["id"]}</td><td class="{item["status"]}">{item["status"]}</td><td>{escape(item["kind"])}</td><td>{escape(item["decision"] or "")}</td>'
                 f'<td>{band_badge(item["confidence_band"], item["confidence"]) if item["confidence_band"] else ""}</td><td>{link}<div class="muted">{detail}</div></td>'
                 f'<td class="time">{escape(readable(item["finished"] or item["created"]))}</td><td>{retry}</td></tr>')
    return "<table><tr><th>Job</th><th>Status</th><th>Kind</th><th>Decision</th><th>Confidence</th><th>Subtitle</th><th>When</th><th></th></tr>" + rows + "</table>"


def layout(title, body, refresh=None):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{meta}'
            f"<title>{escape(title)} - subtitle-guard</title><link rel=\"icon\" href=\"{FAVICON}\"><style>{STYLE}</style></head><body><header><strong>subtitle-guard</strong>"
            f'<a href="/">Dashboard</a><a href="/review">To review</a><a href="/jobs">Jobs</a></header><main><h1>{escape(title)}</h1>{body}</main></body></html>')


def message_box(query):
    return f'<div class="message">{escape(query["message"])}</div>' if query.get("message") else ""


def band_badge(band, score):
    return f'<span class="badge {escape(band or "none")}">{escape(str(score))} {escape(band or "none")}</span>'


def share_colour(share):
    return "#1f7a45" if share >= 0.55 else ("#9a6a00" if share >= 0.35 else "#b3261e")


def first_weak_minute(item):
    return item["weak_minutes"].split(",")[0] if item["weak_minutes"] else 0


def readable(moment):
    return moment.replace("T", " ")


def clock(value):
    return f"{int(value // 60)}:{value % 60:05.2f}"


def seconds(value):
    *larger, whole = value.strip().replace(",", ".").split(":")
    return float(whole) + sum(int(part) * unit for part, unit in zip(reversed(larger), (60, 3600)))


def escape(value):
    return html.escape(str(value), quote=True)
