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
:root { --background: #1a1c1e; --sidebar: #141517; --surface: #222528; --raised: #2a2e32; --text: #e4e5e7; --muted: #8d939a; --line: #33383d;
        --accent: #4aa3df; --good: #3fb871; --medium: #e2a93b; --bad: #e5534b; --reference: #262b31; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--background); color: var(--text); font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.shell { display: grid; grid-template-columns: 300px minmax(0, 1fr); min-height: 100vh; }
aside { background: var(--sidebar); border-right: 1px solid var(--line); padding: 16px 0; position: sticky; top: 0; height: 100vh; overflow-y: auto; }
aside .brand { font-weight: 700; font-size: 16px; padding: 0 18px 14px; color: var(--text); display: block; }
aside nav a { display: block; padding: 7px 18px; color: var(--muted); }
aside nav a.active, aside nav a:hover { color: var(--text); background: var(--surface); text-decoration: none; }
aside h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 18px 18px 6px; }
aside .item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 8px; padding: 7px 18px; border-left: 3px solid transparent; color: var(--text); }
aside .item:hover { background: var(--surface); text-decoration: none; }
aside .item.current { background: var(--raised); border-left-color: var(--accent); }
aside .item .name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
aside .item .why { grid-column: 1 / 3; color: var(--muted); font-size: 12px; }
aside .empty { padding: 4px 18px; color: var(--muted); }
main { padding: 22px 28px; max-width: 1150px; }
h1 { font-size: 19px; margin: 0 0 6px; word-break: break-all; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin: 26px 0 10px; }
.subtle { color: var(--muted); font-size: 12px; margin: 0 0 14px; word-break: break-all; }
.facts { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 10px 0 14px; }
.facts span { color: var(--muted); } .facts b { color: var(--text); font-weight: 600; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; color: var(--text); }
.card:hover { border-color: var(--accent); text-decoration: none; }
.card b { display: block; font-size: 24px; font-weight: 650; }
.card span { color: var(--muted); font-size: 12px; }
table { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
th { color: var(--muted); font-weight: 600; font-size: 12px; background: var(--raised); }
td.time { font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--muted); width: 1%; }
.detail { color: var(--muted); font-size: 12px; margin-top: 2px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; white-space: nowrap; }
.badge.high { background: rgba(63, 184, 113, 0.16); color: var(--good); } .badge.medium { background: rgba(226, 169, 59, 0.16); color: var(--medium); }
.badge.low, .badge.none { background: rgba(229, 83, 75, 0.16); color: var(--bad); }
.status-failed { color: var(--bad); } .status-done { color: var(--good); } .status-running { color: var(--accent); } .status-queued { color: var(--muted); }
.minutes { display: flex; flex-wrap: wrap; gap: 3px; margin: 4px 0 16px; }
.minutes a { width: 14px; height: 26px; border-radius: 7px; display: block; opacity: 0.85; }
.minutes a:hover { opacity: 1; transform: scaleY(1.08); }
.minutes a.good { background: var(--good); } .minutes a.medium { background: var(--medium); } .minutes a.bad { background: var(--bad); }
.minutes a.current { outline: 2px solid var(--text); outline-offset: 1px; opacity: 1; }
.toolbar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
form.inline { display: inline; }
button { padding: 6px 12px; border-radius: 6px; border: 1px solid var(--line); background: var(--raised); color: var(--text); cursor: pointer; font: inherit; }
button:hover { border-color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: #0d1b26; font-weight: 600; }
kbd { font: 11px ui-monospace, monospace; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px; padding: 0 4px; color: var(--muted); }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin: 0 0 16px; }
.panel label { margin-right: 14px; color: var(--muted); font-size: 12px; }
input[type=text] { width: 92px; padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--background); color: var(--text); font: inherit; }
.hint { color: var(--muted); font-size: 12px; margin: 8px 0 0; }
table.reading td:nth-child(2), table.reading td:nth-child(3) { width: 49%; }
table.reading tr.reference td { background: var(--reference); color: #c9ced4; }
table.reading tr.subtitle td:first-child { border-left: 3px solid transparent; }
table.reading tr.subtitle.match td:first-child { border-left-color: var(--good); }
table.reading tr.subtitle.off td:first-child { border-left-color: var(--bad); }
table.reading tr.subtitle.off td:nth-child(3) { color: #f3b4af; }
tr.pickable { cursor: pointer; } tr.pickable:hover td { background: var(--raised); }
.message { background: rgba(74, 163, 223, 0.12); border: 1px solid var(--accent); padding: 8px 12px; border-radius: 6px; margin-bottom: 14px; }
.muted { color: var(--muted); }
.keys { color: var(--muted); font-size: 12px; margin-top: 28px; }
@media (max-width: 860px) {
  .shell { grid-template-columns: 1fr; }
  aside { position: static; height: auto; max-height: 45vh; border-right: 0; border-bottom: 1px solid var(--line); }
  main { padding: 16px; }
  th:nth-child(n+5), td:nth-child(n+5) { display: none; }
  table.reading th, table.reading td { display: table-cell; }
}
"""
KEYS = """
document.addEventListener('keydown', event => {
  if (event.target.matches('input, textarea') || event.metaKey || event.ctrlKey || event.altKey) return;
  const targets = {j: 'next-item', k: 'previous-item', ArrowRight: 'next-minute', ArrowLeft: 'previous-minute'};
  const forms = {a: 'accept-form', r: 'recheck-form'};
  if (targets[event.key] && document.getElementById(targets[event.key])) { window.location = document.getElementById(targets[event.key]).href; }
  if (forms[event.key] && document.getElementById(forms[event.key])) { document.getElementById(forms[event.key]).submit(); }
});
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
            return self.respond(401, layout("Token needed", '<form><input type="text" name="token" placeholder="token"> <button>Open</button></form>', navigation=False))
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
            return self.respond(401, layout("Token needed", "<p>Open the UI with its token first.</p>", navigation=False))
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
    elif action == "move" and video:
        moved = review.move_to_episode(video, path, form.get("episode", ""))
        if moved is None:
            message = "That episode was not found next to this one."
        else:
            return "/subtitle?" + urllib.parse.urlencode({"path": moved[0], "message": f"Moved to {form['episode']}, check job {moved[1]} queued."})
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
    cards = [("to review", len(review.review_items()), "/review")]
    cards += [(f"jobs {status}", number, f"/jobs?status={status}") for status, number in sorted(counts["status"].items())]
    cards += [(f"confidence {band}", number, "/jobs") for band, number in sorted(counts["confidence"].items())]
    body = '<div class="cards">' + "".join(f'<a class="card" href="{link}"><b>{number}</b><span>{escape(label)}</span></a>' for label, number, link in cards) + "</div>"
    body += "<h2>Latest jobs</h2>" + jobs_table(jobs.recent(limit=25))
    return 200, layout("Dashboard", body, refresh=15, page="dashboard"), "text/html; charset=utf-8"


def review_page(query):
    items = review.review_items()
    if not items:
        return 200, layout("To review", message_box(query) + '<p class="muted">Nothing to review. New checks land here when they need a person.</p>', page="review"), \
            "text/html; charset=utf-8"
    rows = "".join(f'<tr><td><a href="{subtitle_link(item["subtitle"], first_weak_minute(item))}">{escape(os.path.basename(item["subtitle"]))}</a>'
                   f'<div class="detail">{escape(os.path.dirname(item["subtitle"]))}</div></td><td>{escape(item["decision"])}</td>'
                   f'<td>{band_badge(item["band"], item["confidence"])}</td><td>{escape(item["weak_minutes"] or "-")}</td><td>{escape(item["note"])}</td>'
                   f'<td class="time">{escape(readable(item["time"]))}</td></tr>' for item in items)
    table = "<table><tr><th>Subtitle</th><th>Decision</th><th>Confidence</th><th>Weak minutes</th><th>Note</th><th>Checked</th></tr>" + rows + "</table>"
    return 200, layout("To review", message_box(query) + table + keys_help(), page="review"), "text/html; charset=utf-8"


def jobs_page(query):
    filters = " ".join(f'<a href="/jobs?status={name}">{name}</a>' for name in jobs.STATUS)
    body = message_box(query) + f'<p class="subtle">Show <a href="/jobs">all</a> {filters}</p>' + jobs_table(jobs.recent(query.get("status"), limit=200), with_retry=True)
    return 200, layout("Jobs", body, refresh=15, page="jobs"), "text/html; charset=utf-8"


def subtitle_page(query):
    path = query.get("path", "")
    if not known_subtitle(path):
        return 404, layout("Unknown subtitle", "<p>This subtitle is not known to the guard.</p>"), "text/html; charset=utf-8"
    video = review.video_for(path)
    minute = int(query.get("minute", "0") or 0)
    view = review.side_by_side(video, path, minute * 60, minute * 60 + WINDOW_SECONDS) if video else {"lines": [], "minutes": [], "reference": None}
    measured = view.get("measure") or {}
    latest = next((row for row in latest_reports() if row["subtitle"] == path), None)
    checked = (f'<span>last check <b>{escape(latest["decision"])}</b> {band_badge(latest["band"], latest["confidence"])} '
               f'<span class="muted">{escape(readable(latest["time"]))}</span></span>') if latest else "<span>never checked</span>"
    facts = [("reference", view["reference"] or "none"), ("matched", measured.get("matched", "-")), ("worst 2-min window", measured.get("worst_window", "-")),
             ("weak minutes", ", ".join(str(value) for value in measured.get("weak_minutes", [])) or "-")]
    summary = (f'<div class="facts">{checked}</div><div class="facts"><span class="muted">measured now</span>'
               + "".join(f"<span>{escape(label)} <b>{escape(value)}</b></span>" for label, value in facts)
               + f'<span>{band_badge(measured.get("confidence_band", "none"), measured.get("confidence", "-"))}</span></div>')
    known_minutes = [number for number, _ in view["minutes"]]
    minutes = "".join(f'<a class="{share_class(share)}{" current" if number == minute else ""}" title="minute {number}: {share:.0%} of lines match" '
                      f'href="{subtitle_link(path, number)}"></a>' for number, share in view["minutes"])
    earlier = [number for number in known_minutes if number < minute]
    later = [number for number in known_minutes if number > minute]
    navigation = (f'<a id="previous-minute" href="{subtitle_link(path, earlier[-1])}" hidden></a>' if earlier else "") + \
                 (f'<a id="next-minute" href="{subtitle_link(path, later[0])}" hidden></a>' if later else "") + neighbour_links(path)
    hidden = f'<input type="hidden" name="path" value="{escape(path)}"><input type="hidden" name="minute" value="{minute}">'
    actions = "".join(f'<form class="inline" method="post" id="{action}-form">{hidden}<input type="hidden" name="action" value="{action}"><button>{label}</button></form>'
                      for action, label in (("accept", "Accept as is"), ("recheck", "Check again"), ("revert", "Restore the original"), ("another", "Ask Bazarr for another")))
    if latest and latest["decision"] == "wrong-episode" and latest["note"]:
        actions += (f'<form class="inline" method="post">{hidden}<input type="hidden" name="action" value="move">'
                    f'<input type="hidden" name="episode" value="{escape(latest["note"])}"><button class="primary">Move to {escape(latest["note"])}</button></form>')
    rows = "".join(f'<tr class="{side} pickable {"" if matched is None else ("match" if matched else "off")}" data-time="{start:.3f}">'
                   f'<td class="time">{clock(start)}</td><td>{escape(text) if side == "reference" else ""}</td>'
                   f'<td>{escape(text) if side == "subtitle" else ""}</td></tr>' for start, side, text, matched in view["lines"])
    shift = (f'<div class="panel"><form method="post">{hidden}<input type="hidden" name="action" value="shift">'
             f'<label>From <input type="text" id="first" name="first" value="{clock(minute * 60)}"></label>'
             f'<label>To <input type="text" id="last" name="last" value="{clock(minute * 60 + WINDOW_SECONDS)}"></label>'
             f'<label>Shift by (s) <input type="text" id="offset" name="offset" value="0"></label><button class="primary">Apply shift</button></form>'
             f'<p class="hint" id="hint">Click a subtitle line, then the reference line it belongs to, to fill in the shift.</p></div>')
    body = (message_box(query) + f'<p class="subtle">{escape(video or "video not found")}</p>' + summary + f'<div class="minutes">{minutes}</div>'
            + f'<div class="toolbar">{actions}</div>' + shift
            + f'<h2>Minute {minute}</h2><table class="reading"><tr><th>Time</th><th>Reference (embedded)</th><th>Subtitle</th></tr>{rows}</table>'
            + "<h2>History</h2>" + jobs_table(jobs.for_subtitle(path)) + navigation + keys_help() + f"<script>{PICKER}</script>")
    return 200, layout(os.path.basename(path), body, current=path), "text/html; charset=utf-8"


def neighbour_links(path):
    queue = [item["subtitle"] for item in review.review_items()]
    if path not in queue:
        return ""
    position = queue.index(path)
    links = ""
    if position > 0:
        links += f'<a id="previous-item" href="{subtitle_link(queue[position - 1])}" hidden></a>'
    if position + 1 < len(queue):
        links += f'<a id="next-item" href="{subtitle_link(queue[position + 1])}" hidden></a>'
    return links


def keys_help():
    return ('<p class="keys"><kbd>j</kbd> <kbd>k</kbd> next or previous subtitle &nbsp; <kbd>Left</kbd> <kbd>Right</kbd> previous or next minute &nbsp; '
            '<kbd>a</kbd> accept &nbsp; <kbd>r</kbd> check again</p>')


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
        detail = escape((item["error"] or item["summary"] or "").replace(" -> ", " to "))
        rows += (f'<tr><td>{item["id"]}</td><td class="status-{item["status"]}">{item["status"]}</td><td>{escape(item["kind"])}</td><td>{escape(item["decision"] or "")}</td>'
                 f'<td>{band_badge(item["confidence_band"], item["confidence"]) if item["confidence_band"] else ""}</td><td>{link}<div class="detail">{detail}</div></td>'
                 f'<td class="time">{escape(readable(item["finished"] or item["created"]))}</td><td>{retry}</td></tr>')
    return "<table><tr><th>Job</th><th>Status</th><th>Kind</th><th>Decision</th><th>Confidence</th><th>Subtitle</th><th>When</th><th></th></tr>" + rows + "</table>"


def layout(title, body, refresh=None, page="", current=None, navigation=True):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    links = "".join(f'<a class="{"active" if page == key else ""}" href="{link}">{label}</a>'
                    for key, link, label in (("dashboard", "/", "Dashboard"), ("review", "/review", "To review"), ("jobs", "/jobs", "Jobs")))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{meta}'
            f'<title>{escape(title)} - subtitle-guard</title><link rel="icon" href="{FAVICON}"><style>{STYLE}</style></head><body><div class="shell">'
            f'<aside><a class="brand" href="/">subtitle-guard</a>{f"<nav>{links}</nav>{sidebar(current)}" if navigation else ""}</aside>'
            f'<main><h1>{escape(title)}</h1>{body}</main></div><script>{KEYS}</script></body></html>')


def sidebar(current):
    items = review.review_items()
    if not items:
        return '<h3>To review</h3><p class="empty">Nothing to review.</p>'
    entries = "".join(f'<a class="item{" current" if item["subtitle"] == current else ""}" title="{escape(item["subtitle"])}" '
                      f'href="{subtitle_link(item["subtitle"], first_weak_minute(item))}"><span class="name">{escape(os.path.basename(item["subtitle"]))}</span>'
                      f'{band_badge(item["band"], item["confidence"])}<span class="why">{escape(reason(item))}</span></a>' for item in items)
    return f"<h3>To review ({len(items)})</h3>{entries}"


def reason(item):
    if item["weak_minutes"]:
        return f"{item['decision']}, weak minutes {item['weak_minutes']}"
    return f"{item['decision']}, {item['note']}" if item["note"] else item["decision"]


def subtitle_link(path, minute=0):
    return "/subtitle?" + urllib.parse.urlencode({"path": path, "minute": minute})


def message_box(query):
    return f'<div class="message">{escape(query["message"])}</div>' if query.get("message") else ""


def band_badge(band, score):
    return f'<span class="badge {escape(band or "none")}">{escape(str(score))} {escape(band or "none")}</span>'


def share_class(share):
    return "good" if share >= 0.55 else ("medium" if share >= 0.35 else "bad")


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
