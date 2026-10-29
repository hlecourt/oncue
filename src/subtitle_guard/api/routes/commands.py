# SPDX-License-Identifier: GPL-3.0-or-later
"""What a person can do to a subtitle: apply its proposal, move lines, restore a saved version, keep it, check it again, ask Bazarr for another
one, delete it, and the same three safe actions on many subtitles at once. Writes need the version of the file the page was showing."""

import os

from fastapi import APIRouter, Depends, HTTPException

from ... import files, jobs, review
from ...errors import EditRefusedError, StaleVersionError
from ...files import file_hash, inside_roots
from ..i18n import tr
from ..lookup import known_path
from ..schemas import BulkAction, BulkResult, CommandResult, Restore, Retime, Shift, VersionGuard
from ..security import require_csrf

router = APIRouter(tags=["commands"], dependencies=[Depends(require_csrf)])
SELECTION_MARGIN_SECONDS = 0.05


@router.post("/subtitles/{subtitle_id}/apply", operation_id="applyProposal")
def apply_proposal(subtitle_id: str, body: VersionGuard) -> CommandResult:
    path, video = writable(subtitle_id)
    outcome = refused_or(lambda: review.apply_proposal(video, path, body.version))
    if outcome is None:
        raise HTTPException(409, {"code": "nothing_to_apply", "message": tr("There is no fix to apply for this subtitle.")})
    return CommandResult(message=tr("The fix was applied and the previous file was backed up."), decision=outcome["decision"])


@router.post("/subtitles/{subtitle_id}/shift", operation_id="shiftLines")
def shift_lines(subtitle_id: str, body: Shift) -> CommandResult:
    path, video = writable(subtitle_id)
    outcome = refused_or(lambda: review.shift_range(video, path, body.first - SELECTION_MARGIN_SECONDS, body.last + SELECTION_MARGIN_SECONDS, body.offset,
                                                    body.version))
    seconds = f"{abs(body.offset):.2f}"
    message = tr("The lines now appear {seconds} s earlier.", seconds=seconds) if body.offset < 0 else tr("The lines now appear {seconds} s later.", seconds=seconds)
    return CommandResult(message=message, decision=outcome["decision"])


@router.post("/subtitles/{subtitle_id}/retime", operation_id="retimeLines")
def retime(subtitle_id: str, body: Retime) -> CommandResult:
    path, video = writable(subtitle_id)
    changes = [(change.first, change.last, change.offset) for change in body.changes]
    outcome = refused_or(lambda: review.retime(video, path, body.version, body.use_proposal, changes))
    return CommandResult(message=tr("Saved. The previous file was backed up."), decision=outcome["decision"])


@router.post("/subtitles/{subtitle_id}/restore", operation_id="restoreVersion")
def restore(subtitle_id: str, body: Restore) -> CommandResult:
    path, _ = writable(subtitle_id)
    refused_or(lambda: review.restore_saved_version(path, body.saved, body.version))
    return CommandResult(message=tr("That version is back, and the one it replaced was backed up."), decision="reverted")


@router.post("/subtitles/{subtitle_id}/keep", operation_id="keepAsIs")
def keep(subtitle_id: str) -> CommandResult:
    path, _ = located(subtitle_id)
    review.accept(path)
    return CommandResult(message=tr("It leaves Review. It comes back only if the file changes."))


@router.post("/subtitles/{subtitle_id}/recheck", operation_id="checkAgain", status_code=202)
def recheck(subtitle_id: str) -> CommandResult:
    path, video = located(subtitle_id)
    return CommandResult(message=tr("It will be checked again in the background."), job=review.recheck(video, path))


@router.post("/subtitles/{subtitle_id}/replacement", operation_id="askForReplacement", status_code=202)
def replacement(subtitle_id: str) -> CommandResult:
    path, video = located(subtitle_id)
    if not review.ask_for_another(video, path):
        raise HTTPException(409, {"code": "not_from_bazarr", "message": tr("This subtitle did not come from Bazarr, so there is no one to ask.")})
    return CommandResult(message=tr("Bazarr is looking for another subtitle. It will be checked when it arrives."))


@router.post("/subtitles/{subtitle_id}/delete", operation_id="deleteSubtitle")
def delete(subtitle_id: str, body: VersionGuard) -> CommandResult:
    path, _ = writable(subtitle_id)
    if file_hash(path) != body.version:
        raise HTTPException(409, {"code": "stale_version", "message": tr("The subtitle changed since this page was opened, reload it and try again.")})
    review.delete_subtitle(path)
    return CommandResult(message=tr("{name} was deleted. A copy was kept in the backups.", name=os.path.basename(path)), decision="deleted")


@router.post("/review/bulk", operation_id="bulkAction")
def bulk(body: BulkAction) -> BulkResult:
    done, skipped = [], {}
    for identity in dict.fromkeys(body.ids):
        try:
            path, video = located(identity)
        except HTTPException as error:
            skipped[identity] = error.detail["message"]
            continue
        if body.action == "keep":
            review.accept(path)
        elif body.action == "recheck":
            review.recheck(video, path)
        elif not review.ask_for_another(video, path):
            skipped[identity] = tr("This subtitle did not come from Bazarr.")
            continue
        done.append(identity)
    return BulkResult(done=done, skipped=skipped)


@router.post("/jobs/{job_id}/retry", operation_id="retryJob", status_code=202)
def retry_job(job_id: int) -> CommandResult:
    item = jobs.job(job_id)
    paths = [item["arguments"][key] for key in ("video", "subtitle") if item and item["arguments"].get(key)] if item else []
    if item is None or not all(inside_roots(path) for path in paths) or not jobs.requeue(job_id):
        raise HTTPException(409, {"code": "not_retryable", "message": tr("Only a finished task inside the allowed folders can be run again.")})
    return CommandResult(message=tr("It will run again shortly."), job=job_id)


def writable(subtitle_id):
    if not files.ROOTS:
        raise HTTPException(409, {"code": "no_allowed_folders", "message": tr("Set SUBTITLE_GUARD_ROOTS to your media folders before changing subtitles "
                                                                              "from the UI.")})
    return located(subtitle_id)


def located(subtitle_id):
    path = known_path(subtitle_id)
    video = review.video_for(path)
    if video is None:
        raise HTTPException(409, {"code": "no_video", "message": tr("The video for this subtitle was not found, nothing was changed.")})
    return path, video


def refused_or(action):
    try:
        return action()
    except StaleVersionError as error:
        raise HTTPException(409, {"code": "stale_version", "message": tr("Nothing was changed: {reason}.", reason=error)}) from error
    except EditRefusedError as error:
        raise HTTPException(422, {"code": "refused", "message": tr("Nothing was changed: {reason}.", reason=error)}) from error

