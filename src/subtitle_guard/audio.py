"""Fallback for videos without an embedded track: sync to the audio with ffsubsync, unverified."""

import json
import os
import shutil
import subprocess
import sys

from .outcome import DECISION
from .storage import backup_path


FFSUBSYNC_LIBRARIES = os.environ.get("SUBTITLE_GUARD_FFSUBSYNC_PATH", "/app/bazarr/bin/libs")
AUDIO_SYNC_MAX_OFFSET_SECONDS = 120
AUDIO_SYNC_EDGE_SHARE = 0.9
AUDIO_SYNC_PLAIN_MAX_OFFSET_SECONDS = 30
AUDIO_SYNC_FORMATS = (".srt", ".ass", ".ssa", ".vtt")
AUDIO_SYNC_SCRIPT = ("import json, sys\nsys.path.insert(0, sys.argv.pop(1))\nfrom ffsubsync.ffsubsync import make_parser, run\n"
                     "result = run(make_parser().parse_args(sys.argv[1:]))\n"
                     "print(json.dumps({key: result.get(key) for key in ('offset_seconds', 'framerate_scale_factor', 'sync_was_successful')}))")


def sync_to_audio(video, subtitle):
    extension = os.path.splitext(subtitle)[1].lower()
    if extension not in AUDIO_SYNC_FORMATS:
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, format not supported by ffsubsync"}
    backup_folder = backup_path(subtitle)
    shutil.copy2(subtitle, os.path.join(backup_folder, "original" + extension))
    output = subtitle + ".subtitle-guard-audio" + extension
    result = run_ffsubsync(video, subtitle, output, fix_framerate=True)
    if result and abs(result["offset_seconds"]) >= AUDIO_SYNC_EDGE_SHARE * AUDIO_SYNC_MAX_OFFSET_SECONDS:
        result = run_ffsubsync(video, subtitle, output, fix_framerate=False)
        if result and abs(result["offset_seconds"]) > AUDIO_SYNC_PLAIN_MAX_OFFSET_SECONDS:
            result = None
    if not result:
        if os.path.exists(output):
            os.remove(output)
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, audio sync not confident"}
    os.replace(output, subtitle)
    shutil.copy2(subtitle, os.path.join(backup_folder, "kept" + extension))
    return {"decision": DECISION["audio_synced"], "reason": f"synced to the audio by ffsubsync, offset {result['offset_seconds']:+.2f}s, "
                                                          f"scale {result['framerate_scale_factor']:.4f}, not verified"}


def run_ffsubsync(video, subtitle, output, fix_framerate):
    command = [sys.executable, "-c", AUDIO_SYNC_SCRIPT, FFSUBSYNC_LIBRARIES, video, "-i", subtitle, "-o", output,
               "--ffmpegpath", os.path.dirname(shutil.which("ffmpeg") or "/usr/bin/ffmpeg"), "--vad", "webrtc",
               "--max-offset-seconds", str(AUDIO_SYNC_MAX_OFFSET_SECONDS), "--skip-sync-on-low-quality",
               "--quality-max-offset-seconds", str(AUDIO_SYNC_MAX_OFFSET_SECONDS), "--max-framerate-deviation", "0.05", "--output-encoding", "same"]
    if not fix_framerate:
        command.append("--no-fix-framerate")
    lines = subprocess.run(command, capture_output=True, text=True).stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        return None
    if not result.get("sync_was_successful") or not os.path.exists(output):
        return None
    return {"offset_seconds": result.get("offset_seconds") or 0.0, "framerate_scale_factor": result.get("framerate_scale_factor") or 1.0}
