# SPDX-License-Identifier: GPL-3.0-or-later
"""What the editor plays and draws: the light audio copy of a video track and silent windows of the picture (streamed with range requests), the
waveform peaks, and a still frame."""

import threading
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import FileResponse, Response

from ...audio_copy import ENCODED_WINDOW_SECONDS, PEAKS_PER_SECOND, copy_paths, played, ready, request_copy, video_window, window_seconds
from ...errors import ToolError
from ...preview import audio_tracks, picture_facts, still_frame, video_duration
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
        facts, _ = picture(video, file_signature(video))
    except ToolError as error:
        raise HTTPException(422, {"code": "unreadable_video", "message": tr("The video cannot be read: {reason}", reason=error)}) from error
    seconds = window_seconds(facts["copyable"]) if facts else None
    if not tracks:
        return AudioState(ready=False, track=None, tracks=[], duration=duration, job=None, failed=False, peaks_per_second=PEAKS_PER_SECOND,
                          window_seconds=seconds)
    chosen = body.track if body.track is not None else tracks[0]["index"]
    if chosen not in [track["index"] for track in tracks]:
        raise HTTPException(422, {"code": "unknown_track", "message": tr("This video has no such audio track.")})
    job, failed = request_copy(video, chosen, body.retry)
    return AudioState(ready=ready(video, chosen), track=chosen, tracks=[AudioTrack(**track) for track in tracks], duration=duration, job=job, failed=failed,
                      peaks_per_second=PEAKS_PER_SECOND, window_seconds=seconds)


# the signature is only part of the cache key, so a changed video is probed again
@lru_cache(maxsize=KNOWN_VIDEOS)
def video_facts(video, signature):
    return audio_tracks(video), video_duration(video)


@lru_cache(maxsize=KNOWN_VIDEOS)
def picture(video, signature):
    return picture_facts(video), video_duration(video)


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


@router.get("/video/{index}", operation_id="streamVideoWindow", dependencies=[Depends(require_user)], response_class=FileResponse)
def stream_video_window(subtitle_id: str, index: int = Path(ge=0, le=MAX_SECONDS // ENCODED_WINDOW_SECONDS)):
    _, video = located(subtitle_id)
    try:
        facts, duration = picture(video, file_signature(video))
        if facts is None:
            raise HTTPException(404, {"code": "no_picture", "message": tr("This video has no picture.")})
        if index * window_seconds(facts["copyable"]) >= duration:
            raise HTTPException(404, {"code": "past_the_end", "message": tr("This part is past the end of the video.")})
        return FileResponse(video_window(video, index, facts["copyable"]), media_type="video/mp4")
    except ToolError as error:
        raise HTTPException(422, {"code": "unreadable_video", "message": tr("The video cannot be read: {reason}", reason=error)}) from error


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
