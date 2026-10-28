# SPDX-License-Identifier: GPL-3.0-or-later
"""History of every check, one row per check in the job database; the old report.tsv is imported once."""

import json
import os
from contextlib import closing

from . import jobs
from .logs import LEVEL, log
from .outcome import DECISION, SETTLED_DECISIONS
from .storage import now, state_path

LEGACY_REPORT = "report.tsv"
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


def report_history(subtitle=None, limit=None):
    query, parameters = "select * from reports", []
    if subtitle:
        query, parameters = query + " where subtitle = ?", [subtitle]
    query += " order by id desc"
    if limit:
        query, parameters = query + " limit ?", [*parameters, limit]
    with closing(connect()) as connection:
        return [as_report(row) for row in connection.execute(query, parameters).fetchall()]


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
        import_legacy_report(connection)
    return connection


def import_legacy_report(connection):
    path = state_path(LEGACY_REPORT)
    rows, skipped = [], 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            row = legacy_row(line.rstrip("\n").split("\t"))
            skipped += row is None
            rows += [row] if row else []
    connection.executemany(f"insert into reports ({', '.join(COLUMNS)}) values ({', '.join('?' * len(COLUMNS))})", rows)
    os.replace(path, path + ".imported")
    log("reports.imported", f"{len(rows)} rows imported from {LEGACY_REPORT}, {skipped} malformed rows skipped", LEVEL["warning"] if skipped else LEVEL["info"],
        imported=len(rows), skipped=skipped, source=path)


def legacy_row(fields):
    if len(fields) < 11:
        return None
    score, band = fields[10].split("/", 1) if len(fields) >= 13 and "/" in fields[10] else ("", "")
    if len(fields) >= 13 and "/" not in fields[10]:
        return None
    extended = len(fields) >= 12
    changes = fields[11] if len(fields) >= 14 else ""
    if changes and not valid_json(changes):
        return None
    return (fields[0], fields[-1], fields[-2], fields[1], fields[2], fields[3], fields[4], fields[8] if extended else "", fields[9] if extended else "",
            score, band, changes, 1)


def valid_json(value):
    try:
        json.loads(value)
    except ValueError:
        return False
    return True


def as_report(row):
    return {**dict(row), "changes": json.loads(row["changes"]) if row["changes"] else None}


def text(value):
    return "" if value is None else str(value)
