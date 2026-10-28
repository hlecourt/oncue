"""Web UI in plain words: what needs a look, what is out of sync and where, and one click to fix it."""

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
NOTICEABLE_SECONDS = 0.5
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%232f5fd0'/%3E"
           "%3Crect x='3' y='9' width='10' height='2' fill='white'/%3E%3Crect x='3' y='12' width='6' height='2' fill='white'/%3E%3C/svg%3E")
DECISION_WORDS = {"kept": "In sync", "fixed": "Fixed automatically", "rejected": "Could not be fixed", "wrong-episode": "Belongs to another episode",
                  "unverified": "Cannot be checked", "audio-synced": "Synced to the audio, not checked", "best-effort": "Best version kept, not fully in sync",
                  "manual": "Adjusted by hand", "reverted": "Back to the original file"}
JOB_WORDS = {"queued": "waiting", "running": "working on it", "done": "done", "failed": "error"}
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
main { padding: 24px 30px; max-width: 1100px; }
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
.parts { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
.parts li { background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--bad); border-radius: 8px; padding: 10px 14px;
            display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; }
.parts li .what { flex: 1 1 260px; }
.parts li b { font-variant-numeric: tabular-nums; }
.activity { list-style: none; padding: 0; margin: 0; }
.activity li { padding: 8px 0; border-bottom: 1px solid var(--line); display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
.activity li .when { color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; min-width: 140px; }
.activity li .error { color: var(--bad); }
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
.minutes { display: flex; gap: 2px; margin: 4px 0 4px; }
.minutes a { flex: 1 1 0; min-width: 3px; height: 30px; border-radius: 3px; display: block; opacity: 0.85; }
.minutes a:hover { opacity: 1; }
.good { background: var(--good); } .medium { background: var(--medium); } .bad { background: var(--bad); } .quiet { background: var(--line); }
.minutes a.current { outline: 2px solid var(--text); outline-offset: 1px; opacity: 1; }
.ends { display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
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
.keys { color: var(--muted); font-size: 12px; margin-top: 28px; }
kbd { font: 11px ui-monospace, monospace; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px; padding: 0 4px; }
@media (max-width: 860px) {
  .shell { grid-template-columns: 1fr; }
  aside { position: static; height: auto; max-height: 40vh; border-right: 0; border-bottom: 1px solid var(--line); }
  main { padding: 16px; }
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
PICKER = """
let picked = null;
const clock = value => `${Math.floor(value / 60)}:${(value % 60).toFixed(2).padStart(5, '0')}`;
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
        message = "It will run again shortly." if jobs.requeue(int(form.get("job", "0"))) else "Only a finished task can be run again."
        return "/jobs?" + urllib.parse.urlencode({"message": message})
    if not known_subtitle(path):
        return "/review?" + urllib.parse.urlencode({"message": "Unknown subtitle, nothing was changed."})
    video = review.video_for(path)
    if video is None:
        return subtitle_link(path, message="The video for this subtitle was not found, nothing was changed.")
    if action in ("fix-part", "shift"):
        first, last, shift = seconds(form["first"]), seconds(form["last"]), float(form["offset"])
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
        moved = review.move_to_episode(video, path, form.get("episode", ""))
        if moved is None:
            message = "That episode was not found next to this one, nothing was changed."
        else:
            path, message = moved[0], f"Moved to {form['episode']}. It will be checked there."
    else:
        message = "Nothing was changed."
    if form.get("back") == "review":
        return "/review?" + urllib.parse.urlencode({"message": f"{os.path.basename(path)}: {message}"})
    return subtitle_link(path, None if action == "move" else form.get("minute", "0"), message)


def known_subtitle(path):
    if not path.lower().endswith(SUBTITLE_EXTENSIONS) or not os.path.isfile(path):
        return False
    return any(row["subtitle"] == path for row in latest_reports()) or bool(jobs.for_subtitle(path, limit=1))


def dashboard_page(query):
    rows = latest_reports()
    needing_a_look = {item["subtitle"] for item in review.review_items()}
    settled = [row for row in rows if row["subtitle"] not in needing_a_look]
    waiting = sum(jobs.counts()["status"].get(status, 0) for status in ("queued", "running"))
    cards = [("need a look", len(needing_a_look), "/review"), ("fixed automatically", count(settled, "fixed"), "/jobs"),
             ("in sync", count(settled, "kept", "manual"), "/jobs"), ("cannot be checked", count(settled, "unverified", "audio-synced"), "/jobs"),
             ("waiting to be checked", waiting, "/jobs?status=queued"), ("errors", jobs.counts()["status"].get("failed", 0), "/jobs?status=failed")]
    body = '<div class="cards">' + "".join(f'<a class="card" href="{link}"><b>{number}</b><span>{escape(label)}</span></a>' for label, number, link in cards) + "</div>"
    body += "<h2>Latest activity</h2>" + activity(jobs.recent(limit=20))
    return 200, layout("Overview", body, refresh=15, page="dashboard"), "text/html; charset=utf-8"


def review_page(query):
    items = review.review_items()
    if not items:
        return 200, layout("Needs a look", message_box(query) + '<div class="status good"><b>Nothing needs a look.</b><span>Subtitles show up here when the guard is not sure.'
                                                                  "</span></div>", page="review"), "text/html; charset=utf-8"
    rows = "".join(f'<tr><td><span class="dot {tone(item)}"></span></td><td><a href="{subtitle_link(item["subtitle"])}">'
                   f'{escape(os.path.basename(item["subtitle"]))}</a><div class="muted">{escape(where(item["subtitle"]))}</div></td>'
                   f'<td>{escape(reason(item))}</td><td>{quick_fix(item)}</td><td class="time">{escape(readable(item["time"]))}</td></tr>' for item in items)
    body = (message_box(query) + "<p class=\"subtle words\">The guard was not sure about these subtitles. Use the button when there is one, "
            "or open the subtitle to see what is wrong.</p>"
            + f"<table><tr><th></th><th>Subtitle</th><th>What happened</th><th></th><th>Checked</th></tr>{rows}</table>" + keys_help())
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


def jobs_page(query):
    filters = " ".join(f'<a href="/jobs?status={name}">{JOB_WORDS[name]}</a>' for name in jobs.STATUS)
    body = message_box(query) + f'<p class="subtle">Show <a href="/jobs">everything</a> {filters}</p>' + activity(jobs.recent(query.get("status"), limit=200), retry=True)
    return 200, layout("Activity", body, refresh=15, page="jobs"), "text/html; charset=utf-8"


def subtitle_page(query):
    path = query.get("path", "")
    if not known_subtitle(path):
        return 404, layout("Unknown subtitle", "<p>This subtitle is not known to the guard.</p>"), "text/html; charset=utf-8"
    video = review.video_for(path)
    if video is None:
        return 200, layout(os.path.basename(path), '<div class="status neutral"><b>The video for this subtitle was not found.</b></div>', current=path), \
            "text/html; charset=utf-8"
    latest = next((row for row in latest_reports() if row["subtitle"] == path), None)
    diagnosis = review.diagnose(video, path)
    minute = int(query["minute"]) if query.get("minute", "").isdigit() else first_problem_minute(diagnosis)
    view = review.side_by_side(video, path, minute * 60, minute * 60 + WINDOW_SECONDS, diagnosis["shifts"] if diagnosis["fixable"] else None)
    hidden = f'<input type="hidden" name="path" value="{escape(path)}"><input type="hidden" name="minute" value="{minute}">'
    body = (message_box(query) + f'<p class="subtle">{escape(where(path))}</p>' + status_box(diagnosis, latest)
            + f'<div class="toolbar">{actions(video, path, diagnosis, latest, hidden)}</div>' + parts_list(path, diagnosis, hidden)
            + timeline(path, view, minute) + reading(view, diagnosis, minute) + by_hand(hidden, minute)
            + technical(path, video, view, latest) + navigation(path, view, minute) + keys_help() + f"<script>{PICKER}</script>")
    return 200, layout(os.path.basename(path), body, current=path), "text/html; charset=utf-8"


def status_box(diagnosis, latest):
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
    if diagnosis["state"] == "out-of-sync" and not parts:
        title, detail = "Some lines are slightly off.", "Fix everything to put them back in place."
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
    if not diagnosis["parts"]:
        return ""
    items = ""
    for part in diagnosis["parts"]:
        direction = "too late" if part["shift"] < 0 else "too early"
        fields = (f'<input type="hidden" name="first" value="{part["first"]:.3f}"><input type="hidden" name="last" value="{part["last"]:.3f}">'
                  f'<input type="hidden" name="offset" value="{part["shift"]:.2f}">')
        items += (f'<li><span class="what"><b>{short_clock(part["first"])} to {short_clock(part["last"])}</b>: {part["lines"]} lines appear '
                  f'{abs(part["shift"]):.1f} s {direction}.</span>'
                  f'<form class="inline" method="post">{hidden}{fields}<input type="hidden" name="action" value="fix-part"><button class="small">Fix this part</button></form>'
                  f'<a href="{subtitle_link(path, int(part["first"] // 60))}">Show</a></li>')
    return f'<h2>What is wrong</h2><ul class="parts">{items}</ul>'


def timeline(path, view, minute):
    if not view["minutes"]:
        return ""
    bars = "".join(f'<a class="{state}{" current" if number == minute else ""}" title="{minute_title(number, share)}" href="{subtitle_link(path, number)}"></a>'
                   for number, share, state in view["minutes"])
    length = len(view["minutes"])
    return (f'<h2>The whole video, minute by minute ({length} min)</h2><p class="legend"><span><i class="good"></i>all lines on time</span>'
            '<span><i class="medium"></i>some lines off</span><span><i class="bad"></i>most lines off</span><span><i class="quiet"></i>almost no lines</span>'
            f'<span>Click a bar to read that minute.</span></p><div class="minutes">{bars}</div><div class="ends"><span>0:00</span><span>{length}:00</span></div>')


def minute_title(number, share):
    return f"minute {number}: almost no lines" if share is None else f"minute {number}: {share:.0%} of lines on time"


def reading(view, diagnosis, minute):
    rows = "".join(moment_row(moment, diagnosis) for moment in view["moments"])
    if not rows:
        return ""
    left = "what the subtitles built into the video say" if view["reference"] else "nothing to compare with"
    return (f"<h2>Minute {minute}, line by line</h2><p class=\"subtle words\">Each row is one moment of the video. On the left, {left}. "
            "On the right, the line of your subtitle that goes with it, and whether it shows on time.</p>"
            f'<table class="reading"><tr><th>Time</th><th>In the video</th><th>Your subtitle</th><th>On time?</th></tr>{rows}</table>')


def moment_row(moment, diagnosis):
    video_cell = escape(moment["video"]) if moment["video"] is not None else '<span class="muted">(no line here)</span>'
    subtitle_cell = escape(moment["subtitle"]) if moment["subtitle"] is not None else '<span class="muted">(no line here)</span>'
    state, label = "", ""
    if moment["subtitle"] is not None and abs(moment["shift"]) >= NOTICEABLE_SECONDS:
        state = "late"
        label = f"{abs(moment['shift']):.1f} s {'too late' if moment['shift'] < 0 else 'too early'}<div class=\"muted\">shows at {short_clock(moment['shown_at'])}</div>"
    elif moment["subtitle"] is not None and moment["video"] is not None and diagnosis["state"] in ("in-sync", "unclear", "out-of-sync"):
        state, label = "fine", "on time"
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
    return f'<details><summary>Technical details</summary><table>{rows}</table><h2>History</h2>{activity(jobs.for_subtitle(path))}</details>'


def navigation(path, view, minute):
    known = [number for number, _, state in view["minutes"] if state != "quiet"]
    links = ""
    earlier, later = [number for number in known if number < minute], [number for number in known if number > minute]
    if earlier:
        links += f'<a id="previous-minute" href="{subtitle_link(path, earlier[-1])}" hidden></a>'
    if later:
        links += f'<a id="next-minute" href="{subtitle_link(path, later[0])}" hidden></a>'
    queue = [item["subtitle"] for item in review.review_items()]
    if path in queue and queue.index(path) > 0:
        links += f'<a id="previous-item" href="{subtitle_link(queue[queue.index(path) - 1])}" hidden></a>'
    if path in queue and queue.index(path) + 1 < len(queue):
        links += f'<a id="next-item" href="{subtitle_link(queue[queue.index(path) + 1])}" hidden></a>'
    return links


def activity(items, retry=False):
    if not items:
        return '<p class="muted">Nothing yet.</p>'
    entries = ""
    for item in items:
        subtitle = item["arguments"].get("subtitle", "")
        name = f'<a href="{subtitle_link(subtitle)}">{escape(os.path.basename(subtitle))}</a>' if subtitle else escape(item["kind"])
        if item["status"] == "failed":
            what = f'<span class="error">error: {escape(item["error"] or "")}</span>'
        elif item["status"] == "done":
            what = escape(DECISION_WORDS.get(item["decision"] or "", (item["decision"] or "done").replace("-", " ")))
        else:
            what = f'<span class="muted">{JOB_WORDS[item["status"]]}</span>'
        button = (f'<form class="inline" method="post"><input type="hidden" name="action" value="retry-job"><input type="hidden" name="job" value="{item["id"]}">'
                  f'<button class="small">Run again</button></form>') if retry and item["status"] in ("done", "failed") else ""
        entries += f'<li><span class="when">{escape(readable(item["finished"] or item["created"]))}</span><span>{name}</span><span>{what}</span>{button}</li>'
    return f'<ul class="activity">{entries}</ul>'


def api_status(query):
    return 200, json.dumps({**jobs.counts(), "to_review": len(review.review_items())}), "application/json"


def api_jobs(query):
    return 200, json.dumps(jobs.recent(query.get("status"), limit=int(query.get("limit", "100")))), "application/json"


def api_review(query):
    return 200, json.dumps(review.review_items()), "application/json"


def layout(title, body, refresh=None, page="", current=None, navigation=True):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    links = "".join(f'<a class="{"active" if page == key else ""}" href="{link}">{label}</a>'
                    for key, link, label in (("dashboard", "/", "Overview"), ("review", "/review", "Needs a look"), ("jobs", "/jobs", "Activity")))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{meta}'
            f'<title>{escape(title)} - subtitle-guard</title><link rel="icon" href="{FAVICON}"><style>{STYLE}</style></head><body><div class="shell">'
            f'<aside><a class="brand" href="/">subtitle-guard</a>{f"<nav>{links}</nav>{sidebar(current)}" if navigation else ""}</aside>'
            f'<main><h1>{escape(title)}</h1>{body}</main></div><script>{KEYS}</script></body></html>')


def sidebar(current):
    items = review.review_items()
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
        return f"Out of sync around {item['weak_minutes'].split(',')[0]}:00"
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
        query["message"] = message
    return "/subtitle?" + urllib.parse.urlencode(query)


def keys_help():
    return '<p class="keys">Keyboard: <kbd>j</kbd> <kbd>k</kbd> next or previous subtitle, <kbd>Left</kbd> <kbd>Right</kbd> previous or next minute, <kbd>a</kbd> keep it.</p>'


def message_box(query):
    return f'<div class="message">{escape(query["message"])}</div>' if query.get("message") else ""


def readable(moment):
    return moment.replace("T", " ")


def clock(value):
    return f"{int(value // 60)}:{value % 60:05.2f}"


def short_clock(value):
    return f"{int(value // 60)}:{int(value % 60):02d}"


def seconds(value):
    *larger, whole = value.strip().replace(",", ".").split(":")
    return float(whole) + sum(int(part) * unit for part, unit in zip(reversed(larger), (60, 3600)))


def escape(value):
    return html.escape(str(value), quote=True)
