"""Building blocks of the pages: layout, tables, badges, timeline, line-by-line view and formatting helpers."""

import html
import json
import os
import re
import urllib.parse
from datetime import date, timedelta
from importlib import resources

from .. import jobs, review
from ..logs import (
    event_level,
    event_message,
)
from ..outcome import DECISION, DECISION_WORDS
from ..reports import report_history
from .context import AUTH, NOTICES, REQUEST, cached, notice


def asset(name):
    return resources.files(__package__).joinpath("assets", name).read_text()


WINDOW_SECONDS = 60


NOTICEABLE_SECONDS = 0.5


FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%232f5fd0'/%3E"
           "%3Crect x='3' y='9' width='10' height='2' fill='white'/%3E%3Crect x='3' y='12' width='6' height='2' fill='white'/%3E%3C/svg%3E")


HISTORY_GROUPS = {"fixed": ("Fixed", ("fixed",)), "in-sync": ("In sync", ("kept",)), "not-fixed": ("Not fixed", ("rejected", "wrong-episode", "best-effort")),
                  "not-checked": ("Not checked", ("unverified", "audio-synced")), "by-hand": ("By hand", ("manual", "reverted"))}


DECISION_TONES = {"kept": "good", "fixed": "good", "manual": "good", "rejected": "bad", "wrong-episode": "bad", "best-effort": "warn",
                  "unverified": "neutral", "audio-synced": "warn", "reverted": "neutral"}


BAND_TONES = {"high": "good", "medium": "warn", "low": "bad", "none": "neutral"}


STARTED_BY = {"hook": "Bazarr", "scan": "Library scan", "watch": "Folder watch", "check": "Check", "manual": "You, by hand", "ui": "You, Fix everything", "revert": "You, restore"}


KIND_WORDS = {"hook": "Check a subtitle from Bazarr", "check": "Check", "blacklist": "Ask Bazarr for another subtitle", "retry-check": "Wait for the other subtitle"}


CHANGES_SHOWN = 3


UNCHANGED_DECISIONS = ("kept", "rejected", "wrong-episode", "unverified")


SPEED_TOLERANCE = 0.001


TASK_STATES = {"running": ("neutral", "Working on it"), "queued": ("neutral", "Waiting"), "failed": ("bad", "Error")}


TICK_MINUTES = (1, 2, 5, 10, 15, 30, 60)


STYLE = asset("style.css")


KEYS = asset("keys.js")


COPY = asset("copy.js")


PICKER = asset("picker.js")


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
        "too-short": ("neutral", "This subtitle cannot be checked.", "It has too few lines, which is normal for subtitles that only translate signs."),
        "wrong-ends": ("bad", "The end times of this subtitle are wrong.", "Many lines stay on screen far too long. Try another subtitle."),
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
