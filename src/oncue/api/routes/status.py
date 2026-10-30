# SPDX-License-Identifier: GPL-3.0-or-later
"""Counts the front polls to know when to refresh, and the liveness and readiness probes."""

import os
import sqlite3

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse

from ... import __version__, jobs, review
from ...heartbeats import SUPERVISED_VARIABLE, worker_health
from ...logs import LEVEL, log
from ..schemas import Readiness, Status
from ..security import require_user

router = APIRouter()
probes = APIRouter()


@router.get("/api/status", operation_id="getStatus", tags=["status"], dependencies=[Depends(require_user)])
def get_status() -> Status:
    counts = jobs.counts()
    return Status(version=__version__, jobs=counts["status"], confidence=counts["confidence"], to_review=len(review.review_items()))


@probes.get("/health", include_in_schema=False)
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


@probes.get("/ready", include_in_schema=False, response_model=Readiness)
def ready():
    try:
        jobs.counts()
    except sqlite3.Error as error:
        log("api.not-ready", f"database unavailable: {type(error).__name__}: {error}", LEVEL["error"])
        return JSONResponse(Readiness(ready=False, database="unavailable").model_dump(), status_code=503)
    workers = worker_health()
    supervised = os.environ.get(SUPERVISED_VARIABLE) == "1"
    state = Readiness(ready=all(workers.values()) and (bool(workers) or not supervised), database="ok", workers=workers)
    return JSONResponse(state.model_dump(), status_code=200 if state.ready else 503)
