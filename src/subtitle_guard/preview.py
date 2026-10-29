# SPDX-License-Identifier: GPL-3.0-or-later
"""Short audio clips and still frames of a video, for listening to a subtitle in the editor."""

import os
import tempfile

from .errors import ProbeError
from .reference import probe_json
from .storage import state_path
from .tools import run_tool, stderr_tail

CLIP_SECONDS = 60
CLIP_SAMPLE_RATE = 16000
PREVIEW_SECONDS = 20
FRAME_WIDTH = 640


def audio_tracks(video):
    streams = probe_json(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "a",
                          "-show_entries", "stream=index,codec_name,channels:stream_tags=language,title:stream_disposition=default,comment", "-of", "json", video],
                         video).get("streams", [])
    tracks = [{"index": stream["index"], "language": stream.get("tags", {}).get("language", ""), "title": stream.get("tags", {}).get("title", ""),
               "codec": stream.get("codec_name", ""), "channels": stream.get("channels", 0), "default": bool(stream.get("disposition", {}).get("default")),
               "commentary": bool(stream.get("disposition", {}).get("comment"))} for stream in streams]
    return sorted(tracks, key=lambda track: (track["commentary"], not track["default"], track["index"]))


def video_duration(video):
    duration = probe_json(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries", "format=duration", "-of", "json", video],
                          video).get("format", {}).get("duration")
    return float(duration) if duration else 0.0


def audio_clip(video, start, track):
    # first_pts=0 pads the clip when the audio stream starts after the video, so clip time + start stays the subtitle clock
    return extract(["-ss", f"{start:.3f}", "-i", video, "-t", str(CLIP_SECONDS), "-map", f"0:{track}", "-vn", "-sn", "-dn",
                    "-af", f"aresample={CLIP_SAMPLE_RATE}:async=1:first_pts=0", "-ac", "1", "-c:a", "pcm_s16le", "-map_metadata", "-1", "-f", "wav"],
                   ".wav", video)


def still_frame(video, moment):
    return extract(["-ss", f"{moment:.3f}", "-i", video, "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale='min({FRAME_WIDTH},iw)':-2",
                    "-an", "-sn", "-dn", "-q:v", "5", "-f", "image2", "-c:v", "mjpeg"], ".jpg", video)


def extract(arguments, suffix, video):
    folder = state_path("preview-work")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    descriptor, output = tempfile.mkstemp(suffix=suffix, dir=folder)
    os.close(descriptor)
    try:
        result = run_tool(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file", "-threads", "1", *arguments, "-y", output],
                          PREVIEW_SECONDS)
        if result.returncode != 0 or os.path.getsize(output) == 0:
            raise ProbeError(f"ffmpeg could not read {video}: {stderr_tail(result) or 'empty output'}")
        with open(output, "rb") as handle:
            return handle.read()
    finally:
        os.remove(output)
