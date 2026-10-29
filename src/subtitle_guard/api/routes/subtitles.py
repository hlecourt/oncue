# SPDX-License-Identifier: GPL-3.0-or-later
"""One subtitle: what the guard knows about it, its proposal and parts, its saved versions, and its lines next to the video's."""

import os
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from ... import review
from ...catalog import known_ids
from ...catalog import subtitle_id as known_id
from ...files import file_hash, inside_roots
from ...library import SUBTITLE_EXTENSIONS, external_subtitles
from ...reports import latest_reports, report_history
from ...storage import backup_folders
from ...subtitles import parse_subtitle
from ..lookup import known_path, usable
from ..presentation import folder_label, result_words
from ..schemas import Line, Lines, Part, Sibling, SubtitleDetail, SubtitleList, SubtitleRow, Version
from ..security import require_user
from .review import current_proposal, summary

BACKUP_FOLDER_FORMAT = "%Y%m%d-%H%M%S-%f"
router = APIRouter(prefix="/subtitles", tags=["subtitles"], dependencies=[Depends(require_user)])


@router.get("", operation_id="listSubtitles")
def list_subtitles(q: str = Query("", max_length=200), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)) -> SubtitleList:
    words = q.lower().split()
    found = [row for row in latest_reports() if listable(row["subtitle"]) and all(word in row["subtitle"].lower() for word in words)]
    ids = known_ids()
    rows = []
    for row in found[offset:offset + limit]:
        result, tone = result_words(row)
        rows.append(SubtitleRow(id=ids.get(row["subtitle"]) or known_id(row["subtitle"]), name=os.path.basename(row["subtitle"]), folder=folder_label(row["subtitle"]),
                                words=result, tone=tone, confidence=int(row["confidence"]) if row["confidence"] else None, band=row["band"] or "none",
                                checked=row["time"]))
    return SubtitleList(rows=rows, total=len(found))


def listable(path):
    return path.lower().endswith(SUBTITLE_EXTENSIONS) and not os.path.islink(path) and inside_roots(path)


@router.get("/{subtitle_id}", operation_id="getSubtitle")
def get_subtitle(subtitle_id: str) -> SubtitleDetail:
    path = known_path(subtitle_id)
    video = review.video_for(path)
    latest = next(iter(report_history(path, limit=1)), None)
    words, tone = result_words(latest) if latest else ("Not checked yet", "neutral")
    proposal = current_proposal(path)
    diagnosis = review.diagnose(video, path) if video else None
    changes = (proposal or {}).get("changes") or {}
    parts = changes.get("parts") or (diagnosis or {}).get("parts") or []
    return SubtitleDetail(id=subtitle_id, name=os.path.basename(path), folder=folder_label(path), video=os.path.basename(video) if video else None,
                          version=file_hash(path), decision=latest["decision"] if latest else None, words=words, tone=tone,
                          confidence=int(latest["confidence"]) if latest and latest["confidence"] else None, band=(latest or {}).get("band") or "none",
                          checked=latest["time"] if latest else None, reference=(latest or {}).get("reference") or "", proposal=summary(proposal),
                          parts=[Part(**{key: part[key] for key in ("first", "last", "lines", "shift")}) for part in parts],
                          scale=changes.get("scale") or (diagnosis or {}).get("scale") or 1.0, versions=versions(path),
                          from_bazarr=bool(video) and review.came_from_bazarr(video, path), siblings=siblings(path, video))


def siblings(path, video):
    if not video:
        return []
    folder = os.path.dirname(path)
    found = []
    for name in sorted(external_subtitles(os.path.basename(video), os.listdir(folder))):
        other = os.path.join(folder, name)
        if other == path or not usable(other) or review.video_for(other) != video:
            continue
        latest = next(iter(report_history(other, limit=1)), None)
        words, tone = result_words(latest) if latest else ("Not checked yet", "neutral")
        found.append(Sibling(id=known_id(other, video), name=name, words=words, tone=tone))
    return found


@router.get("/{subtitle_id}/lines", operation_id="getSubtitleLines")
def get_lines(subtitle_id: str) -> Lines:
    path = known_path(subtitle_id)
    video = review.video_for(path)
    cues = parse_subtitle(path)["cues"]
    reference = review.readable_reference(video, path, len(cues)) if video else None
    texts = (review.reference_texts(video, reference) or [(start, end, "") for start, end in zip(reference["starts"], reference["ends"], strict=False)]) if reference else []
    proposal = current_proposal(path)
    return Lines(version=file_hash(path), lines=[Line(start=start, end=end, text=review.clean(text)) for start, end, text in cues],
                 reference=[Line(start=float(start), end=float(end), text=review.clean(text)) for start, end, text in texts],
                 proposal=proposal["timings"] if proposal else None)


def versions(path):
    extension = os.path.splitext(path)[1]
    found = []
    for folder in reversed(backup_folders(path)):
        for name in sorted(os.listdir(folder)):
            if name.endswith(extension):
                found.append(Version(id=f"{os.path.basename(folder)}/{name}", label=name[: -len(extension)], time=folder_time(os.path.basename(folder))))
    return found


def folder_time(name):
    try:
        return datetime.strptime(name, BACKUP_FOLDER_FORMAT).isoformat(timespec="seconds")
    except ValueError:
        return name
