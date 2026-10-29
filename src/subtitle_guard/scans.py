# SPDX-License-Identifier: GPL-3.0-or-later
"""A scan of the whole library, through Bazarr when it is set up or through the watched folders: one check job per subtitle, with progress and
a way to stop it. Only one scan runs at a time."""

import os
from contextlib import closing

from . import configuration, jobs
from .bazarr import api_key, library_subtitles
from .configuration import MODE
from .errors import ConfigError
from .library import library_items
from .logs import log
from .storage import now

STATE = {"listing": "listing", "checking": "checking", "stopped": "stopped"}


def start_scan():
    with closing(jobs.connect()) as connection:
        connection.execute("begin immediate")
        running = connection.execute("select id from scans where status in (?, ?)", (STATE["listing"], STATE["checking"])).fetchone()
        if running:
            connection.execute("commit")
            return running["id"]
        scan_id = connection.execute("insert into scans (started, status, total, source) values (?, ?, 0, ?)",
                                     (now(), STATE["listing"], scan_source())).lastrowid
        connection.execute("commit")
    jobs.enqueue(jobs.KIND["scan"], {"scan": scan_id})
    log("scan.started", f"library scan {scan_id} started from {scan_source()}", scan=scan_id)
    return scan_id


def run_scan(scan_id):
    apply = configuration.value("mode") == MODE["fix"]
    total = 0
    for video, subtitle, neighbours in scan_items():
        if scan(scan_id)["status"] == STATE["stopped"]:
            break
        jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle, "apply": apply, "neighbours": [list(pair) for pair in neighbours], "scan": scan_id})
        total += 1
        with closing(jobs.connect()) as connection:
            connection.execute("update scans set total = ? where id = ?", (total, scan_id))
    with closing(jobs.connect()) as connection:
        connection.execute("update scans set status = ? where id = ? and status = ?", (STATE["checking"], scan_id, STATE["listing"]))
    log("scan.listed", f"library scan {scan_id} queued {total} subtitles", scan=scan_id, total=total)
    return {"decision": "listed", "summary": f"{total} subtitles queued"}


def stop_scan(scan_id):
    with closing(jobs.connect()) as connection:
        connection.execute("update scans set status = ? where id = ?", (STATE["stopped"], scan_id))
        dropped = connection.execute("delete from jobs where status = ? and kind = ? and json_extract(arguments, '$.scan') = ?",
                                     (jobs.STATUS["queued"], jobs.KIND["check"], scan_id)).rowcount
    log("scan.stopped", f"library scan {scan_id} stopped, {dropped} waiting checks dropped", scan=scan_id, dropped=dropped)


def scan(scan_id):
    with closing(jobs.connect()) as connection:
        row = connection.execute("select * from scans where id = ?", (scan_id,)).fetchone()
        if row is None:
            return None
        statuses = dict(connection.execute("select status, count(*) from jobs where kind = ? and json_extract(arguments, '$.scan') = ? group by status",
                                           (jobs.KIND["check"], scan_id)).fetchall())
        in_review = connection.execute("select count(*) from jobs where kind = ? and json_extract(arguments, '$.scan') = ? and decision not in (?, ?)",
                                       (jobs.KIND["check"], scan_id, "kept", "fixed")).fetchone()[0]
    done = statuses.get(jobs.STATUS["done"], 0) + statuses.get(jobs.STATUS["failed"], 0)
    finished = row["status"] == STATE["checking"] and done >= row["total"]
    return {"id": row["id"], "started": row["started"], "source": row["source"], "status": "finished" if finished else row["status"], "total": row["total"],
            "done": done, "failed": statuses.get(jobs.STATUS["failed"], 0), "not_settled": in_review}


def latest_scan():
    with closing(jobs.connect()) as connection:
        row = connection.execute("select id from scans order by id desc limit 1").fetchone()
    return scan(row["id"]) if row else None


def scan_source():
    return "bazarr" if bazarr_configured() else "watched folders"


def scan_items():
    if bazarr_configured():
        yield from library_subtitles()
        return
    for folder in configuration.value("watched_folders"):
        if os.path.isdir(folder):
            yield from library_items(folder)


def bazarr_configured():
    try:
        return bool(api_key())
    except (ConfigError, OSError):
        return False
