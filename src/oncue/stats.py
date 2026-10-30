# SPDX-License-Identifier: GPL-3.0-or-later
"""What OnCue did for the library, computed from the history of checks and tasks: counts, distributions, providers and records."""

import itertools
import json
import math
import os
import re
import statistics
import time
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta

from . import jobs, storage
from .audio_copy import CACHE_BYTES
from .files import folder_bytes
from .heartbeats import worker_health
from .outcome import DECISION
from .storage import state_path

HEATMAP_WEEKS = 26
CONFIDENCE_STEP = 5
LATE_EDGES = (-math.inf, -30, -12, -5, -2, -1, -0.5, 0, 0.5, 1, 2, 5, 12, 30, math.inf)
SPEED_TOLERANCE = 0.001
KNOWN_RATES = ((25, 23.976), (23.976, 25), (24, 23.976), (23.976, 24), (25, 24), (24, 25), (30, 29.97), (29.97, 30))
RATE_TOLERANCE = 0.0015
CHECK_TIME_STEP = 0.5
CHECK_TIME_BINS = 10
NOT_PROVIDERS = {"", "scan", "watch", "check", "manual", "ui", "revert", "hook"}
UNDOING = (DECISION["reverted"], DECISION["manual"], DECISION["deleted"])
WRITTEN_BY_HAND = (DECISION["approved"], DECISION["manual"])
FIX_FOUND = (DECISION["fixed"], DECISION["proposed"])
VERDICTS = {DECISION["kept"]: "in_sync", DECISION["fixed"]: "sure_fix", DECISION["proposed"]: "review", DECISION["rejected"]: "rejected",
            DECISION["wrong_episode"]: "rejected", DECISION["best_effort"]: "rejected"}
LANGUAGE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")
NOT_LANGUAGES = {"hi", "sdh", "cc", "forced", "default", "srt", "ass", "ssa", "vtt"}
FOLDER_SIZE_SECONDS = 300
MAX_PERIODS = 8
PERIODS = {}
FOLDER_SIZES = {}


def dashboard(days=None, today=None):
    today = today or date.today()
    key = (state_path(), days, last_report_id(), today)
    if key not in PERIODS:
        if len(PERIODS) >= MAX_PERIODS:
            PERIODS.clear()
        PERIODS[key] = summarize_period(days, today)
    return {**PERIODS[key], "now": current_activity()}


def summarize_period(days, today):
    since = (datetime.combine(today, datetime.min.time()) - timedelta(days=days - 1)).isoformat(timespec="seconds") if days else ""
    rows = period_reports(since)
    latest = list({row["subtitle"]: row for row in rows}.values())
    fixes = [row for row in rows if row["decision"] in FIX_FOUND]
    written = [row for row in rows if written_fix(row)]
    automatic = [row for row in rows if automatic_fix(row)]
    shifts = [shift for shift in (main_shift(row) for row in fixes) if shift is not None]
    return {
        "subtitles": sum(row["decision"] != DECISION["deleted"] for row in latest), "checks": len(rows),
        "fixed_on_their_own": len(automatic), "found_not_written": sum(row["decision"] == DECISION["fixed"] and not row["applied"] for row in rows),
        "lines_realigned": sum(moved_lines(row) for row in written), "median_offset": round(statistics.median(abs(shift) for shift in shifts), 2) if shifts else None,
        "undone": undone(rows), "per_day": per_day(rows, today), "weekly": weekly(rows, today),
        "confidence": confidence_bins(fixes), "offsets": offset_bins(shifts), "providers": providers(rows),
        "languages": tally(language(row["subtitle"]) for row in latest), "references": tally(reference_kind(row["reference"]) for row in latest),
        "speed_fixes": tally(label for label in (speed_label(row) for row in fixes) if label),
        "check_time": check_time(rows), "records": records(rows, written, since),
    }


def last_report_id():
    with closing(jobs.connect()) as connection:
        return connection.execute("select max(id) from reports").fetchone()[0]


def tally(names):
    return [{"name": name, "count": count} for name, count in Counter(names).most_common()]


def period_reports(since):
    with closing(jobs.connect()) as connection:
        rows = connection.execute("select * from reports where time >= ? order by id", (since,)).fetchall()
    return [{**dict(row), "changes": json.loads(row["changes"]) if row["changes"] else {}} for row in rows]


def automatic_fix(row):
    return row["decision"] == DECISION["fixed"] and row["applied"] and row["provider"] not in ("manual", "ui", "revert")


def written_fix(row):
    return (row["decision"] == DECISION["fixed"] and row["applied"]) or row["decision"] in WRITTEN_BY_HAND


def main_shift(row):
    parts = row["changes"].get("parts") or []
    return max(parts, key=lambda part: part["lines"])["shift"] if parts else None


def moved_lines(row):
    changes = row["changes"]
    if not changes.get("parts") and abs(changes.get("scale", 1.0) - 1) > SPEED_TOLERANCE:
        return changes.get("lines", 0)
    return sum(part["lines"] for part in changes.get("parts") or [])


def undone(rows):
    pending, count = set(), 0
    for row in rows:
        if automatic_fix(row):
            pending.add(row["subtitle"])
        elif row["decision"] in UNDOING and row["subtitle"] in pending:
            pending.discard(row["subtitle"])
            count += 1
    return count


def per_day(rows, today):
    first = today - timedelta(days=HEATMAP_WEEKS * 7 - 1)
    counts = Counter(row["time"][:10] for row in rows if row["time"][:10] >= first.isoformat())
    return [{"day": (first + timedelta(days=offset)).isoformat(), "checks": counts.get((first + timedelta(days=offset)).isoformat(), 0)}
            for offset in range(HEATMAP_WEEKS * 7)]


def weekly(rows, today):
    days = per_day(rows, today)
    return [sum(day["checks"] for day in days[week * 7:(week + 1) * 7]) for week in range(HEATMAP_WEEKS)]


def confidence_bins(fixes):
    counts = [0] * (100 // CONFIDENCE_STEP)
    for row in fixes:
        if row["confidence"] not in (None, ""):
            counts[min(int(float(row["confidence"])) // CONFIDENCE_STEP, len(counts) - 1)] += 1
    return counts


def offset_bins(shifts):
    late = [-shift for shift in shifts]
    return [{"low": None if math.isinf(low) else low, "high": None if math.isinf(high) else high, "count": sum(low <= value < high for value in late)}
            for low, high in itertools.pairwise(LATE_EDGES)]


def providers(rows):
    found = {}
    for row in rows:
        if row["provider"] in NOT_PROVIDERS:
            continue
        entry = found.setdefault(row["provider"], {"provider": row["provider"], "downloads": 0, "in_sync": 0, "sure_fix": 0, "review": 0, "rejected": 0,
                                                    "unchecked": 0, "shifts": []})
        entry["downloads"] += 1
        entry[VERDICTS.get(row["decision"], "unchecked")] += 1
        if row["decision"] in FIX_FOUND and main_shift(row) is not None:
            entry["shifts"].append(abs(main_shift(row)))
    for entry in found.values():
        shifts = entry.pop("shifts")
        entry["median_offset"] = round(statistics.median(shifts), 2) if shifts else None
    return sorted(found.values(), key=lambda entry: -entry["downloads"])


def language(path):
    for token in reversed(os.path.basename(path).lower().split(".")[1:-1]):
        if LANGUAGE.match(token) and token not in NOT_LANGUAGES:
            return token
    return ""


def reference_kind(reference):
    if not reference:
        return "none"
    if reference == "audio":
        return "sound"
    return "picture" if reference.endswith("image") else "text"


def speed_label(row):
    scale = row["changes"].get("scale", 1.0)
    if abs(scale - 1) <= SPEED_TOLERANCE:
        return None
    rate = 1 / scale
    known = next((f"{first:g} / {second:g} fps" for first, second in KNOWN_RATES if abs(rate - first / second) < RATE_TOLERANCE), None)
    return known or f"{(rate - 1) * 100:+.1f}%"


def check_time(rows):
    seconds = sorted(row["seconds"] for row in rows if row.get("seconds") is not None)
    if not seconds:
        return {"median": None, "p95": None, "bins": [0] * CHECK_TIME_BINS}
    bins = [0] * CHECK_TIME_BINS
    for value in seconds:
        bins[min(int(value / CHECK_TIME_STEP), CHECK_TIME_BINS - 1)] += 1
    return {"median": round(statistics.median(seconds), 2), "p95": round(seconds[min(len(seconds) - 1, math.ceil(0.95 * len(seconds)) - 1)], 2), "bins": bins}


def records(rows, written, since):
    largest = max(written, key=lambda row: abs(main_shift(row) or 0), default=None)
    most_parts = max(written, key=lambda row: len(row["changes"].get("parts") or []), default=None)
    seconds = [row["seconds"] for row in rows if row.get("seconds") is not None]
    with closing(jobs.connect()) as connection:
        asked = connection.execute("select count(*) from jobs where kind = ? and created >= ?", (jobs.KIND["blacklist"], since)).fetchone()[0]
        retried = connection.execute("select decision from jobs where kind = ? and status = ? and created >= ?",
                                     (jobs.KIND["replacement_check"], jobs.STATUS["done"], since)).fetchall()
    return {"largest_offset": abs(main_shift(largest)) if largest and main_shift(largest) else None,
            "most_parts": len(most_parts["changes"].get("parts") or []) if most_parts else 0, "fastest_check": min(seconds) if seconds else None,
            "replacements": asked, "replacements_in_sync": sum(row["decision"] == DECISION["kept"] for row in retried), "replacements_checked": len(retried),
            "edits_by_hand": sum(row["decision"] == DECISION["manual"] for row in rows)}


def current_activity():
    with closing(jobs.connect()) as connection:
        statuses = dict(connection.execute("select status, count(*) from jobs group by status").fetchall())
        last = connection.execute("select max(time) from reports").fetchone()[0]
    supervisor = storage.read_json(state_path("supervisor.json")) if os.path.exists(state_path("supervisor.json")) else {}
    workers = worker_health()
    return {"queued": statuses.get(jobs.STATUS["queued"], 0), "running": statuses.get(jobs.STATUS["running"], 0), "workers": sum(workers.values()),
            "workers_expected": len(workers), "last_check": last, "audio_cache_bytes": recent_folder_bytes(state_path("audio-copies")),
            "audio_cache_limit": CACHE_BYTES, "backups_bytes": recent_folder_bytes(storage.BACKUP_DIRECTORY), "started": (supervisor or {}).get("started") if workers else None}


def recent_folder_bytes(folder):
    measured = FOLDER_SIZES.get(folder)
    if measured and time.monotonic() - measured[0] < FOLDER_SIZE_SECONDS:
        return measured[1]
    size = folder_bytes(folder)
    FOLDER_SIZES[folder] = (time.monotonic(), size)
    return size
