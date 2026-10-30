# SPDX-License-Identifier: GPL-3.0-or-later
"""The Dashboard page: what OnCue did over a period, and what it is doing now."""

from fastapi import APIRouter, Depends, Query

from ...stats import dashboard
from ..schemas import StatsView
from ..security import require_user

router = APIRouter(tags=["stats"])


@router.get("/stats", operation_id="getStats", dependencies=[Depends(require_user)])
def get_stats(days: int | None = Query(None, ge=1, le=3650)) -> StatsView:
    return StatsView(**dashboard(days))
