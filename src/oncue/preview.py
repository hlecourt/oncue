# SPDX-License-Identifier: GPL-3.0-or-later
"""Short audio clips and still frames of a video, for listening to a subtitle in the editor."""

import os
import tempfile

from .errors import ProbeError
from .reference import MEDIA_INPUT_OPTIONS, probe_json, run_on_video
from .storage import state_path
from .tools import stderr_tail

PREVIEW_SECONDS = 20
FRAME_WIDTH = 1280
BROWSER_PIXEL_FORMATS = ("yuv420p", "yuvj420p")


def audio_tracks(video):
    streams = probe_json(["ffprobe", "-v", "error", *MEDIA_INPUT_OPTIONS, "-select_streams", "a",
                          "-show_entries", "stream=index,codec_name,channels:stream_tags=language,title:stream_disposition=default,comment", "-of", "json", video],
                         video).get("streams", [])
    tracks = [{"index": stream["index"], "language": stream.get("tags", {}).get("language", ""), "title": stream.get("tags", {}).get("title", ""),
               "codec": stream.get("codec_name", ""), "channels": stream.get("channels", 0), "default": bool(stream.get("disposition", {}).get("default")),
               "commentary": bool(stream.get("disposition", {}).get("comment"))} for stream in streams]
    return sorted(tracks, key=lambda track: (track["commentary"], not track["default"], track["index"]))


def picture_facts(video):
    streams = probe_json(["ffprobe", "-v", "error", *MEDIA_INPUT_OPTIONS, "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt", "-of", "json",
                          video], video).get("streams", [])
    if not streams:
        return None
    return {"copyable": streams[0].get("codec_name") == "h264" and streams[0].get("pix_fmt") in BROWSER_PIXEL_FORMATS}


def video_duration(video):
    duration = probe_json(["ffprobe", "-v", "error", *MEDIA_INPUT_OPTIONS, "-show_entries", "format=duration", "-of", "json", video],
                          video).get("format", {}).get("duration")
    return float(duration) if duration else 0.0


def still_frame(video, moment):
    return extract(["-ss", f"{moment:.3f}", "-i", video, "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale='min({FRAME_WIDTH},iw)':-2",
                    "-an", "-sn", "-dn", "-q:v", "2", "-f", "image2", "-c:v", "mjpeg"], ".jpg", video)


def extract(arguments, suffix, video):
    folder = state_path("preview-work")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    descriptor, output = tempfile.mkstemp(suffix=suffix, dir=folder)
    os.close(descriptor)
    try:
        result = run_on_video(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *MEDIA_INPUT_OPTIONS, "-threads", "1", *arguments, "-y", output],
                              video, PREVIEW_SECONDS)
        if result.returncode != 0 or os.path.getsize(output) == 0:
            raise ProbeError(f"ffmpeg could not read {video}: {stderr_tail(result) or 'empty output'}")
        with open(output, "rb") as handle:
            return handle.read()
    finally:
        os.remove(output)
