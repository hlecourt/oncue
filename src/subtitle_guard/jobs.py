"""Persistent job queue in SQLite, shared by the Bazarr hook, the workers and the web UI."""

import json
import os
import sqlite3
import time
from contextlib import closing

from .logs import log
from .storage import now, state_path

STATUS = {"queued": "queued", "running": "running", "done": "done", "failed": "failed"}
KIND = {"hook": "hook", "check": "check", "blacklist": "blacklist", "retry_check": "retry-check"}
SCHEMA = (
    "create table if not exists jobs (id integer primary key, kind text not null, arguments text not null, status text not null, "
    "run_after real not null, created text not null, started text, finished text, attempts integer not null default 0, worker integer, "
    "decision text, confidence integer, confidence_band text, summary text, error text)",
    "create index if not exists jobs_waiting on jobs (status, run_after)",
    "create table if not exists seen (path text primary key, signature text not null, updated text not null)",
    "create table if not exists reviews (path text primary key, signature text not null, accepted text not null)",
)


def enqueue(kind, arguments, delay_seconds=0):
    with closing(connect()) as connection:
        cursor = connection.execute("insert into jobs (kind, arguments, status, run_after, created) values (?, ?, ?, ?, ?)",
                                    (kind, json.dumps(arguments), STATUS["queued"], time.time() + delay_seconds, now()))
    log("job.queued", f"job {cursor.lastrowid} queued kind={kind}" + (f" delay={delay_seconds}s" if delay_seconds else ""), job=cursor.lastrowid, kind=kind,
        subtitle=arguments.get("subtitle"), delay_seconds=delay_seconds)
    return cursor.lastrowid


def claim(worker):
    with closing(connect()) as connection:
        connection.execute("begin immediate")
        row = connection.execute("select * from jobs where status = ? and run_after <= ? order by run_after, id limit 1",
                                 (STATUS["queued"], time.time())).fetchone()
        if row is None:
            connection.execute("commit")
            return None
        connection.execute("update jobs set status = ?, started = ?, attempts = attempts + 1, worker = ? where id = ?",
                           (STATUS["running"], now(), worker, row["id"]))
        connection.execute("commit")
        return {**as_job(row), "status": STATUS["running"], "attempts": row["attempts"] + 1}


def finish(job_id, outcome):
    with closing(connect()) as connection:
        connection.execute("update jobs set status = ?, finished = ?, decision = ?, confidence = ?, confidence_band = ?, summary = ?, error = null "
                           "where id = ?", (STATUS["done"], now(), outcome.get("decision"), outcome.get("confidence"),
                                            outcome.get("confidence_band"), outcome.get("summary"), job_id))


def fail(job_id, error):
    with closing(connect()) as connection:
        connection.execute("update jobs set status = ?, finished = ?, error = ? where id = ?", (STATUS["failed"], now(), error, job_id))


def requeue(job_id):
    with closing(connect()) as connection:
        cursor = connection.execute("update jobs set status = ?, run_after = ?, error = null where id = ? and status in (?, ?)",
                                    (STATUS["queued"], time.time(), job_id, STATUS["done"], STATUS["failed"]))
        return cursor.rowcount == 1


def recover_running():
    with closing(connect()) as connection:
        return connection.execute("update jobs set status = ? where status = ?", (STATUS["queued"], STATUS["running"])).rowcount


def job(job_id):
    with closing(connect()) as connection:
        row = connection.execute("select * from jobs where id = ?", (job_id,)).fetchone()
        return as_job(row) if row else None


def recent(status=None, limit=50):
    with closing(connect()) as connection:
        if status:
            rows = connection.execute("select * from jobs where status = ? order by id desc limit ?", (status, limit)).fetchall()
        else:
            rows = connection.execute("select * from jobs order by id desc limit ?", (limit,)).fetchall()
        return [as_job(row) for row in rows]


def counts():
    with closing(connect()) as connection:
        by_status = dict(connection.execute("select status, count(*) from jobs group by status").fetchall())
        by_band = dict(connection.execute("select confidence_band, count(*) from jobs where status = ? and confidence_band is not null "
                                          "group by confidence_band", (STATUS["done"],)).fetchall())
        return {"status": by_status, "confidence": by_band}


def seen(path):
    with closing(connect()) as connection:
        row = connection.execute("select signature from seen where path = ?", (path,)).fetchone()
        return row["signature"] if row else None


def remember(path, signature):
    with closing(connect()) as connection:
        connection.execute("insert into seen (path, signature, updated) values (?, ?, ?) "
                           "on conflict (path) do update set signature = excluded.signature, updated = excluded.updated", (path, signature, now()))


def accept(path, signature):
    with closing(connect()) as connection:
        connection.execute("insert into reviews (path, signature, accepted) values (?, ?, ?) "
                           "on conflict (path) do update set signature = excluded.signature, accepted = excluded.accepted", (path, signature, now()))


def accepted():
    with closing(connect()) as connection:
        return dict(connection.execute("select path, signature from reviews").fetchall())


def for_subtitle(path, limit=20):
    return [item for item in recent(limit=1000) if item["arguments"].get("subtitle") == path][:limit]


def connect():
    database = state_path("jobs.sqlite3")
    os.makedirs(os.path.dirname(database), exist_ok=True)
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma busy_timeout = 30000")
    connection.execute("pragma journal_mode = wal")
    for statement in SCHEMA:
        connection.execute(statement)
    return connection


def as_job(row):
    return {**dict(row), "arguments": json.loads(row["arguments"])}
