# SPDX-License-Identifier: GPL-3.0-or-later
"""The subtitles OnCue knows, each with an opaque id, and the proposal waiting on each one for one version of its file."""

import json
import secrets
from contextlib import closing

from . import jobs
from .storage import now

ID_BYTES = 6


def subtitle_id(path, video=""):
    with closing(jobs.connect()) as connection:
        connection.execute("insert into subtitles (id, path, video, updated) values (?, ?, ?, ?) on conflict (path) do update set "
                           "video = coalesce(nullif(excluded.video, ''), subtitles.video), updated = excluded.updated",
                           (secrets.token_hex(ID_BYTES), path, video, now()))
        return connection.execute("select id from subtitles where path = ?", (path,)).fetchone()["id"]


def known_ids():
    with closing(jobs.connect()) as connection:
        return {row["path"]: row["id"] for row in connection.execute("select path, id from subtitles").fetchall()}


def path_for(identity):
    with closing(jobs.connect()) as connection:
        row = connection.execute("select path from subtitles where id = ?", (identity,)).fetchone()
    return row["path"] if row else None


def save_proposal(path, video, version, kind, timings, outcome):
    details = {key: outcome.get(key) for key in ("confidence", "confidence_band", "changes", "reference", "reason")}
    with closing(jobs.connect()) as connection:
        connection.execute("insert into proposals (subtitle_id, version, kind, timings, details, created) values (?, ?, ?, ?, ?, ?) "
                           "on conflict (subtitle_id) do update set version = excluded.version, kind = excluded.kind, timings = excluded.timings, "
                           "details = excluded.details, created = excluded.created",
                           (subtitle_id(path, video), version, kind, json.dumps([[round(start, 3), round(end, 3)] for start, end, _ in timings]),
                            json.dumps(details), now()))


def proposal_for(path, version):
    with closing(jobs.connect()) as connection:
        row = connection.execute("select proposals.* from proposals join subtitles on subtitles.id = proposals.subtitle_id where subtitles.path = ?",
                                 (path,)).fetchone()
        if row is None:
            return None
        if row["version"] != version:
            connection.execute("delete from proposals where subtitle_id = ?", (row["subtitle_id"],))
            return None
    return {"version": row["version"], "kind": row["kind"], "timings": json.loads(row["timings"]), **json.loads(row["details"])}


def discard_proposal(path):
    with closing(jobs.connect()) as connection:
        connection.execute("delete from proposals where subtitle_id in (select id from subtitles where path = ?)", (path,))
