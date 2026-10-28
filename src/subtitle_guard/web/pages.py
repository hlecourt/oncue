"""The pages of the UI and its JSON API, each a function of the query returning status, body and content type."""

import json
import os

from .. import jobs, review
from ..logs import (
    log_file,
    recent_events,
)
from ..reports import latest_reports, report_history
from .actions import known_subtitle
from .context import DIAGNOSES, cached
from .parts import (
    HISTORY_GROUPS,
    PICKER,
    WINDOW_SECONDS,
    actions,
    by_hand,
    count,
    escape,
    first_problem_minute,
    fix_section,
    history_table,
    keys_help,
    layout,
    message_box,
    parts_list,
    queue_links,
    readable,
    reading,
    reason,
    status_box,
    subtitle_link,
    tab_links,
    tasks_now,
    technical,
    technical_log,
    timeline,
    tone,
    where,
)

MAX_MINUTE_DIGITS = 5


LOG_LIMIT = 300


HISTORY_LIMIT = 300


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


def log_page(query):
    search = (f'<form class="filters"><input type="text" name="q" placeholder="Search a file, an event, a word..." value="{escape(query.get("q", ""))}">'
              '<button>Search</button></form>')
    body = search + technical_log(filtered_events(recent_events(), query)[:LOG_LIMIT])
    return 200, layout("Log", body, refresh=None if query else 15, page="log"), "text/html; charset=utf-8"


def filtered_events(events, query):
    text = query.get("q", "").lower()
    return [event for event in events if not text or text in json.dumps(event, ensure_ascii=False).lower()]


def raw_log(query):
    return 200, open(log_file()).read() if os.path.exists(log_file()) else "", "text/plain; charset=utf-8"


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


def api_status(query):
    return 200, json.dumps({**jobs.counts(), "to_review": len(cached("review", review.review_items))}), "application/json"


def api_jobs(query):
    limit = int(query["limit"]) if query.get("limit", "").isdigit() and len(query["limit"]) <= 4 else 100
    return 200, json.dumps(jobs.recent(query.get("status"), limit=max(1, min(limit, 1000)))), "application/json"


def api_log(query):
    return 200, json.dumps(filtered_events(recent_events(), query)[:LOG_LIMIT]), "application/json"


def api_review(query):
    return 200, json.dumps(cached("review", review.review_items)), "application/json"
