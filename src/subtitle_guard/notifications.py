# SPDX-License-Identifier: GPL-3.0-or-later
"""The webhook digest: at most once per interval, one JSON message listing the subtitles that reached Review since the last one."""

import json
import os
import time
import urllib.error
import urllib.request
from contextlib import closing

from . import configuration, jobs
from .logs import LEVEL, log
from .reports import needs_review
from .storage import read_json, state_path, write_json

STATE_FILE = "webhook.json"
SEND_SECONDS = 10
MAX_ITEMS = 50


def send_due():
    url = configuration.value("webhook_url")
    if not url:
        return False
    sent = read_json(state_path(STATE_FILE)) if os.path.exists(state_path(STATE_FILE)) else {"time": 0, "report": latest_report_id()}
    if time.time() - sent["time"] < configuration.value("webhook_minutes") * 60:
        return False
    rows = new_reports(sent["report"])
    items = [row for row in rows if needs_review(row)]
    last = rows[-1]["id"] if rows else sent["report"]
    if not items:
        write_json(state_path(STATE_FILE), {**sent, "report": last})
        return False
    delivered = post(url, {"count": len(items), "subtitles": [{"name": os.path.basename(row["subtitle"]), "folder": os.path.dirname(row["subtitle"]),
                                                                "decision": row["decision"], "time": row["time"]} for row in items[:MAX_ITEMS]]})
    write_json(state_path(STATE_FILE), {"time": time.time(), "report": last if delivered else sent["report"]})
    return delivered


def post(url, message):
    request = urllib.request.Request(url, data=json.dumps(message).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with OPENER.open(request, timeout=SEND_SECONDS) as response:
            log("webhook.sent", f"digest of {message['count']} subtitles sent, status {response.status}", count=message["count"], status=response.status)
            return True
    except (OSError, ValueError) as error:
        log("webhook.failed", f"webhook digest not delivered: {error}", LEVEL["warning"], error=str(error))
        return False


def new_reports(after):
    with closing(jobs.connect()) as connection:
        rows = connection.execute("select * from reports where id > ? and id in (select max(id) from reports group by subtitle) order by id", (after,)).fetchall()
    return [dict(row) for row in rows]


def latest_report_id():
    with closing(jobs.connect()) as connection:
        return connection.execute("select coalesce(max(id), 0) from reports").fetchone()[0]


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, f"refused a redirect to {new_url}", headers, response)


OPENER = urllib.request.build_opener(NoRedirects)
