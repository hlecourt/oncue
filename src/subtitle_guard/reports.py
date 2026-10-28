# SPDX-License-Identifier: GPL-3.0-or-later
"""History of every check, one row per check in the job database; the old report.tsv is imported once."""

import fcntl
import json
import math
import os
import re
from contextlib import closing, contextmanager
from datetime import datetime

from . import jobs
from .logs import LEVEL, log
from .outcome import DECISION, SETTLED_DECISIONS
from .storage import now, state_path

LEGACY_REPORT = "report.tsv"
FIRST_FORMAT_FIELDS = 8
LATER_FORMAT_FIELDS = (11, 12, 13, 14)
DECISION_PATTERN = re.compile(r"[a-z][a-z-]{1,30}")
WEAK_MINUTES_PATTERN = re.compile(r"(\d+(,\d+)*)?")
BANDS = ("", "high", "medium", "low", "none")
COLUMNS = ("time", "subtitle", "provider", "decision", "reference", "matched_before", "matched", "weak_minutes", "note", "confidence", "band", "changes",
           "applied")


def report(subtitle, provider, outcome, applied=True):
    before, after = outcome.get("before") or {}, outcome.get("after") or {}
    values = (now(), subtitle, provider, outcome["decision"], outcome.get("reference", ""), text(before.get("matched")), text(after.get("matched")),
              ",".join(str(minute) for minute in outcome.get("weak_minutes", [])), outcome.get("matches", "") or outcome.get("reason", ""),
              text(outcome.get("confidence")), outcome.get("confidence_band", ""), json.dumps(outcome["changes"]) if outcome.get("changes") else "",
              int(applied))
    with closing(connect()) as connection:
        connection.execute(f"insert into reports ({', '.join(COLUMNS)}) values ({', '.join('?' * len(COLUMNS))})", values)


def latest_reports():
    with closing(connect()) as connection:
        rows = connection.execute("select * from reports where id in (select max(id) from reports group by subtitle) order by subtitle").fetchall()
    return [as_report(row) for row in rows if os.path.exists(row["subtitle"])]


def report_history(subtitle=None, limit=None, decisions=None):
    conditions, parameters = [], []
    if subtitle:
        conditions, parameters = ["subtitle = ?"], [subtitle]
    if decisions:
        conditions.append(f"decision in ({', '.join('?' * len(decisions))})")
        parameters += list(decisions)
    query = "select * from reports" + (" where " + " and ".join(conditions) if conditions else "") + " order by id desc"
    if limit:
        query, parameters = query + " limit ?", [*parameters, limit]
    with closing(connect()) as connection:
        return [as_report(row) for row in connection.execute(query, parameters).fetchall()]


def decision_counts():
    with closing(connect()) as connection:
        return dict(connection.execute("select decision, count(*) from reports group by decision").fetchall())


def has_report(subtitle):
    with closing(connect()) as connection:
        return connection.execute("select 1 from reports where subtitle = ? limit 1", (subtitle,)).fetchone() is not None


def needs_review(row):
    if not row["applied"] and row["decision"] == DECISION["fixed"]:
        return True
    return row["decision"] not in SETTLED_DECISIONS or bool(row["weak_minutes"]) or row["band"] == "low"


def print_needs_human():
    for row in latest_reports():
        if needs_review(row):
            print(f"{row['decision']:13s} {row['time']}  confidence {row['confidence'] or '-'} {row['band']}  weak minutes {row['weak_minutes'] or '-'}  "
                  f"{row['note']}  {row['subtitle']}")
    return 0


def connect():
    connection = jobs.connect()
    if os.path.exists(state_path(LEGACY_REPORT)):
        with import_lock():
            import_legacy_report(connection)
    return connection


@contextmanager
def import_lock():
    with open(state_path(LEGACY_REPORT + ".lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def import_legacy_report(connection):
    path = state_path(LEGACY_REPORT)
    if not os.path.exists(path):
        return
    if connection.execute("select 1 from migrations where name = ?", (LEGACY_REPORT,)).fetchone():
        os.replace(path, path + ".imported")
        return
    rows, skipped = [], 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            row = legacy_row(line.rstrip("\n").split("\t"))
            skipped += row is None
            rows += [row] if row else []
    connection.execute("begin immediate")
    try:
        connection.executemany(f"insert into reports ({', '.join(COLUMNS)}) values ({', '.join('?' * len(COLUMNS))})", rows)
        connection.execute("insert into migrations (name, applied) values (?, ?)", (LEGACY_REPORT, now()))
        connection.execute("commit")
    except BaseException:
        connection.execute("rollback")
        raise
    os.replace(path, path + ".imported")
    log("reports.imported", f"{len(rows)} rows imported from {LEGACY_REPORT}, {skipped} malformed rows skipped", LEVEL["warning"] if skipped else LEVEL["info"],
        imported=len(rows), skipped=skipped, file=path)


def legacy_row(fields):
    if len(fields) == FIRST_FORMAT_FIELDS:
        row = (fields[0], fields[7], fields[6], fields[1], fields[2], fields[3], fields[4], "", "", "", "", "", 1)
        return row if valid_row(row) else None
    if len(fields) not in LATER_FORMAT_FIELDS or (len(fields) >= 13 and "/" not in fields[10]):
        return None
    score, band = fields[10].split("/", 1) if len(fields) >= 13 else ("", "")
    changes = fields[11] if len(fields) == 14 else ""
    extended = len(fields) >= 12
    row = (fields[0], fields[-1], fields[-2], fields[1], fields[2], fields[3], fields[4], fields[8] if extended else "", fields[9] if extended else "",
           score, band, changes, 1)
    return row if valid_row(row) else None


def valid_row(row):
    moment, subtitle, _, decision, _, matched_before, matched, weak_minutes, _, score, band, changes, _ = row
    return (valid_time(moment) and bool(subtitle) and DECISION_PATTERN.fullmatch(decision) is not None and all(valid_number(value) for value in (matched_before, matched))
            and (not score or score.isdigit()) and band in BANDS and WEAK_MINUTES_PATTERN.fullmatch(weak_minutes) is not None and (not changes or valid_changes(changes)))


def valid_time(moment):
    try:
        datetime.fromisoformat(moment)
    except ValueError:
        return False
    return True


def valid_number(value):
    if not value:
        return True
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def valid_changes(value):
    try:
        changes = json.loads(value)
    except ValueError:
        return False
    return isinstance(changes, dict) and isinstance(changes.get("parts"), list) and valid_number(str(changes.get("scale", "")))


def as_report(row):
    return {**dict(row), "changes": json.loads(row["changes"]) if row["changes"] else None}


def text(value):
    return "" if value is None else str(value)
