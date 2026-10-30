# SPDX-License-Identifier: GPL-3.0-or-later
"""What the editor plays and draws: the light audio copy of a video track (streamed with range requests), its waveform peaks, and a still frame."""

import threading
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from ...audio_copy import PEAKS_PER_SECOND, copy_paths, played, ready, request_copy
from ...errors import ToolError
from ...preview import audio_tracks, still_frame, video_duration
from ...storage import file_signature
from ..i18n import tr
from ..schemas import AudioRequest, AudioState, AudioTrack
from ..security import require_csrf, require_user
from .commands import located

router = APIRouter(prefix="/subtitles/{subtitle_id}", tags=["media"])
FRAME_SLOT = threading.Lock()
MAX_SECONDS = 48 * 3600
KNOWN_VIDEOS = 32


@router.post("/audio", operation_id="prepareAudio", dependencies=[Depends(require_csrf)])
def prepare_audio(subtitle_id: str, body: AudioRequest) -> AudioState:
    _, video = located(subtitle_id)
    try:
        tracks, duration = video_facts(video, file_signature(video))
    except ToolError as error:
        raise HTTPException(422, {"code": "unreadable_video", "message": tr("The video cannot be read: {reason}", reason=error)}) from error
    if not tracks:
        return AudioState(ready=False, track=None, tracks=[], duration=duration, job=None, failed=False, peaks_per_second=PEAKS_PER_SECOND)
    chosen = body.track if body.track is not None else tracks[0]["index"]
    if chosen not in [track["index"] for track in tracks]:
        raise HTTPException(422, {"code": "unknown_track", "message": tr("This video has no such audio track.")})
    job, failed = request_copy(video, chosen, body.retry)
    return AudioState(ready=ready(video, chosen), track=chosen, tracks=[AudioTrack(**track) for track in tracks], duration=duration, job=job, failed=failed,
                      peaks_per_second=PEAKS_PER_SECOND)


# the signature is only part of the cache key, so a changed video is probed again
@lru_cache(maxsize=KNOWN_VIDEOS)
def video_facts(video, signature):
    return audio_tracks(video), video_duration(video)


@router.get("/audio/{track}", operation_id="streamAudio", dependencies=[Depends(require_user)], response_class=FileResponse)
def stream_audio(subtitle_id: str, track: int):
    _, video = located(subtitle_id)
    if not ready(video, track):
        raise HTTPException(404, {"code": "not_ready", "message": tr("The audio copy is not ready yet.")})
    return FileResponse(played(video, track), media_type="audio/mp4")


@router.get("/audio/{track}/peaks", operation_id="audioPeaks", dependencies=[Depends(require_user)], response_class=FileResponse)
def audio_peaks(subtitle_id: str, track: int):
    _, video = located(subtitle_id)
    if not ready(video, track):
        raise HTTPException(404, {"code": "not_ready", "message": tr("The audio copy is not ready yet.")})
    return FileResponse(copy_paths(video, track)[1], media_type="application/octet-stream")


@router.get("/frame", operation_id="stillFrame", dependencies=[Depends(require_user)], response_class=Response)
def frame(subtitle_id: str, time: float = Query(ge=0, le=MAX_SECONDS)):
    _, video = located(subtitle_id)
    if not FRAME_SLOT.acquire(blocking=False):
        raise HTTPException(503, {"code": "busy", "message": tr("Busy, try again.")}, headers={"Retry-After": "1"})
    try:
        return Response(still_frame(video, time), media_type="image/jpeg")
    except ToolError as error:
        raise HTTPException(422, {"code": "unreadable_video", "message": tr("The video cannot be read: {reason}", reason=error)}) from error
    finally:
        FRAME_SLOT.release()
