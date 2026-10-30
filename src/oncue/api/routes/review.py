# SPDX-License-Identifier: GPL-3.0-or-later
"""Review: the subtitles waiting for a person's decision, proposals closest to the threshold first."""

import os

from fastapi import APIRouter, Depends

from ... import review
from ...catalog import proposal_for, subtitle_id
from ...files import file_hash
from ..presentation import change_sentences, folder_label, review_reason, review_tone
from ..schemas import ProposalSummary, ReviewItem
from ..security import require_user

router = APIRouter(prefix="/review", tags=["review"], dependencies=[Depends(require_user)])


@router.get("", operation_id="listReview")
def list_review() -> list[ReviewItem]:
    items = [review_item(row) for row in review.review_items()]
    proposals = sorted((item for item in items if item.proposal), key=lambda item: item.proposal.confidence, reverse=True)
    return proposals + sorted((item for item in items if not item.proposal), key=lambda item: item.checked, reverse=True)


def review_item(row):
    path = row["subtitle"]
    video = review.video_for(path)
    proposal = current_proposal(path)
    return ReviewItem(id=subtitle_id(path, video or ""), name=os.path.basename(path), folder=folder_label(path), decision=row["decision"],
                      reason=review_reason(row, proposal), tone=review_tone(row, proposal), confidence=int(row["confidence"]) if row["confidence"] else None,
                      band=row["band"] or "none", checked=row["time"], proposal=summary(proposal),
                      from_bazarr=bool(video) and review.came_from_bazarr(video, path))


def current_proposal(path):
    try:
        return proposal_for(path, file_hash(path))
    except OSError:
        return None


def summary(proposal):
    if proposal is None:
        return None
    return ProposalSummary(kind=proposal["kind"], confidence=proposal.get("confidence") or 0, band=proposal.get("confidence_band") or "none",
                           changes=change_sentences(proposal.get("changes"), "fixed"))
