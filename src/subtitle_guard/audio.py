"""Fallback for videos without an embedded track: sync to the audio with ffsubsync, unverified."""

import json
import os
import shutil
import sys
import tempfile

from .logs import LEVEL, log
from .outcome import DECISION
from .files import read_regular, replace_file, save_version
from .storage import backup_path, file_signature, heavy_disk_read
from .tools import AUDIO_SYNC_SECONDS, run_tool, stderr_tail
from .subtitles import parse_subtitle


FFSUBSYNC_LIBRARIES = os.environ.get("SUBTITLE_GUARD_FFSUBSYNC_PATH", "/app/bazarr/bin/libs")
AUDIO_SYNC_MAX_OFFSET_SECONDS = 120
AUDIO_SYNC_EDGE_SHARE = 0.9
AUDIO_SYNC_PLAIN_MAX_OFFSET_SECONDS = 30
AUDIO_SYNC_FORMATS = (".srt", ".ass", ".ssa", ".vtt")
AUDIO_SYNC_SCRIPT = ("import json, sys\nsys.path.insert(0, sys.argv.pop(1))\nfrom ffsubsync.ffsubsync import make_parser, run\n"
                     "result = run(make_parser().parse_args(sys.argv[1:]))\n"
                     "print(json.dumps({key: result.get(key) for key in ('offset_seconds', 'framerate_scale_factor', 'sync_was_successful')}))")


def sync_to_audio(video, subtitle, signature):
    extension = os.path.splitext(subtitle)[1].lower()
    if extension not in AUDIO_SYNC_FORMATS:
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, format not supported by ffsubsync"}
    descriptor, output = tempfile.mkstemp(prefix=".subtitle-guard-audio-", suffix=extension, dir=os.path.dirname(os.path.abspath(subtitle)))
    os.close(descriptor)
    try:
        return synced_outcome(video, subtitle, signature, output)
    finally:
        if os.path.exists(output):
            os.remove(output)


def synced_outcome(video, subtitle, signature, output):
    result = run_ffsubsync(video, subtitle, output, fix_framerate=True)
    if result and abs(result["offset_seconds"]) >= AUDIO_SYNC_EDGE_SHARE * AUDIO_SYNC_MAX_OFFSET_SECONDS:
        result = run_ffsubsync(video, subtitle, output, fix_framerate=False)
        if result and abs(result["offset_seconds"]) > AUDIO_SYNC_PLAIN_MAX_OFFSET_SECONDS:
            result = None
    if not result:
        log("audio.sync-rejected", "ffsubsync gave no usable result (failed or offset beyond limit), file unchanged", LEVEL["warning"], subtitle=subtitle)
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, audio sync not confident"}
    if file_signature(subtitle) != signature:
        return {"decision": DECISION["unverified"], "reason": "the subtitle changed during the check, it was left as it is and will be checked again"}
    starts = [start for start, _, _ in parse_subtitle(subtitle)["cues"]]
    backup_folder = backup_path(subtitle)
    save_version(subtitle, backup_folder, "original")
    replace_file(subtitle, read_regular(output))
    log("audio.synced", f"ffsubsync applied offset={result['offset_seconds']:+.2f}s scale={result['framerate_scale_factor']:.4f}", subtitle=subtitle,
        backup=backup_folder, **result)
    save_version(subtitle, backup_folder, "kept")
    moved = [{"first": starts[0], "last": starts[-1], "lines": len(starts), "shift": round(result["offset_seconds"], 2)}] if starts else []
    return {"decision": DECISION["audio_synced"], "reason": f"synced to the audio by ffsubsync, offset {result['offset_seconds']:+.2f}s, "
                                                          f"scale {result['framerate_scale_factor']:.4f}, not verified",
            "changes": {"scale": result["framerate_scale_factor"], "lines": len(starts), "parts": moved}}


def run_ffsubsync(video, subtitle, output, fix_framerate):
    command = [sys.executable, "-c", AUDIO_SYNC_SCRIPT, FFSUBSYNC_LIBRARIES, video, "-i", subtitle, "-o", output,
               "--ffmpegpath", os.path.dirname(shutil.which("ffmpeg") or "/usr/bin/ffmpeg"), "--vad", "webrtc",
               "--max-offset-seconds", str(AUDIO_SYNC_MAX_OFFSET_SECONDS), "--skip-sync-on-low-quality",
               "--quality-max-offset-seconds", str(AUDIO_SYNC_MAX_OFFSET_SECONDS), "--max-framerate-deviation", "0.05", "--output-encoding", "same"]
    if not fix_framerate:
        command.append("--no-fix-framerate")
    with heavy_disk_read():
        finished = run_tool(command, AUDIO_SYNC_SECONDS)
    lines = finished.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        result = {}
    if finished.returncode != 0 or not result.get("sync_was_successful") or not os.path.exists(output):
        log("audio.ffsubsync-failed", f"ffsubsync exit={finished.returncode} successful={result.get('sync_was_successful')}", LEVEL["warning"], subtitle=subtitle,
            stderr=stderr_tail(finished))
        return None
    return {"offset_seconds": result.get("offset_seconds") or 0.0, "framerate_scale_factor": result.get("framerate_scale_factor") or 1.0}
