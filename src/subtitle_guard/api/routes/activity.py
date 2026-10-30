# SPDX-License-Identifier: GPL-3.0-or-later
"""What happened: every check with its result, the tasks in the queue, and the technical log."""

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ... import jobs
from ...catalog import subtitle_id
from ...logs import event_level, event_message, log_file, recent_events
from ...reports import decision_counts, report_history
from ..i18n import tr
from ..lookup import known_path
from ..presentation import HISTORY_GROUPS, change_sentences, folder_label, result_words, started_by
from ..schemas import History, HistoryGroup, HistoryRow, Job, LogEvent
from ..security import require_user

router = APIRouter(tags=["activity"], dependencies=[Depends(require_user)])
MAX_ROWS = 200
LOG_FIELDS = ("time", "level", "source", "event", "message")


@router.get("/history", operation_id="getHistory")
def get_history(result: str | None = None, before: int | None = None, limit: int = Query(50, ge=1, le=MAX_ROWS), subtitle: str | None = None) -> History:
    decisions = HISTORY_GROUPS[result][1] if result in HISTORY_GROUPS else None
    rows = report_history(subtitle=known_path(subtitle) if subtitle else None, limit=limit + 1, decisions=decisions, before=before)
    counts = decision_counts()
    groups = [HistoryGroup(key=key, label=tr(label), count=sum(counts.get(decision, 0) for decision in members)) for key, (label, members) in HISTORY_GROUPS.items()]
    shown = rows[:limit]
    return History(rows=[history_row(row) for row in shown], groups=groups, next_before=shown[-1]["id"] if len(rows) > limit else None)


@router.get("/jobs", operation_id="listJobs")
def list_jobs(status: str | None = None, limit: int = Query(50, ge=1, le=MAX_ROWS)) -> list[Job]:
    return [Job(**{key: item.get(key) for key in Job.model_fields if key != "subtitle"}, subtitle=os.path.basename(item["arguments"].get("subtitle", "")) or None)
            for item in jobs.recent(status, limit=limit)]


@router.get("/log", operation_id="getLog")
def get_log(q: str = Query("", max_length=200), level: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> list[LogEvent]:
    text = q.lower()
    events = [event for event in recent_events() if (not text or text in json.dumps(event, ensure_ascii=False).lower())
              and (not level or event_level(event) == level)][:limit]
    return [LogEvent(time=event.get("time", ""), level=event_level(event), source=event.get("source", ""), event=event["event"], message=event_message(event),
                     details={key: value for key, value in event.items() if key not in LOG_FIELDS}) for event in events]


@router.get("/log/export", operation_id="exportLog", response_class=FileResponse)
def export_log():
    path = log_file()
    if not os.path.exists(path):
        raise HTTPException(404, {"code": "no_log", "message": tr("Nothing logged yet.")})
    return FileResponse(path, media_type="application/x-ndjson", filename="subtitle-guard.log.jsonl")


def history_row(row):
    words, tone = result_words(row)
    exists = os.path.isfile(row["subtitle"])
    changes = change_sentences(row["changes"], row["decision"])
    return HistoryRow(id=row["id"], time=row["time"], subtitle_id=subtitle_id(row["subtitle"]) if exists else None, name=os.path.basename(row["subtitle"]),
                      folder=folder_label(row["subtitle"]), decision=row["decision"], words=words, tone=tone, started_by=started_by(row["provider"]),
                      changes=changes, confidence=int(row["confidence"]) if row["confidence"] else None,
                      band=row["band"] or "none")
