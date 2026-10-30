# SPDX-License-Identifier: GPL-3.0-or-later
"""Review, the reading side: the subtitles waiting for a person, the video each belongs to, the diagnosis of its timing and the text of the
reference track. What a person does about them is in review_actions."""

import hashlib
import os
import re
from contextlib import suppress

from . import jobs
from .alignment import MAX_WRONG_END_SHARE, MIN_CUES, describe_changes, judge, oversized_reason, wrong_end_share
from .errors import MatroskaError, ToolError
from .files import inside_allowed_folders
from .library import VIDEO_EXTENSIONS
from .logs import LEVEL, log
from .matroska import matroska_texts
from .outcome import DECISION
from .reference import MEDIA_INPUT_OPTIONS, load_reference, probe_json, run_on_video
from .reports import latest_reports, needs_review
from .state import load_state
from .storage import file_signature, heavy_disk_read, read_json, state_path, write_json
from .subtitles import is_hearing_impaired, parse_subtitle, parse_timed_text
from .tools import EXTRACT_SECONDS, stderr_tail

MARKUP = re.compile(r"\{[^}]{0,500}\}|<[^>]{1,500}>")
DIAGNOSIS_CACHE_VERSION = 3


def review_items():
    accepted = jobs.accepted()
    return [row for row in latest_reports() if needs_review(row) and accepted.get(row["subtitle"]) != file_signature(row["subtitle"])]


def video_for(subtitle):
    folder, name = os.path.split(subtitle)
    candidates = [os.path.join(folder, video) for video in os.listdir(folder)
                  if video.lower().endswith(VIDEO_EXTENSIONS) and name.startswith(os.path.splitext(video)[0] + ".")]
    safe = [video for video in candidates if not os.path.islink(video) and os.path.isfile(video) and inside_allowed_folders(video)]
    return max(safe, key=len) if safe else None


def readable_reference(video, subtitle, cue_count):
    try:
        return load_reference(video, is_hearing_impaired(subtitle), cue_count)
    except ToolError as error:
        log("review.reference-unreadable", f"video unreadable for the review page: {error}", LEVEL["warning"], video=video, subtitle=subtitle)
        return None


def diagnose(video, subtitle):
    cache_file = diagnosis_file(video, subtitle)
    if os.path.exists(cache_file):
        with suppress(ValueError):
            return read_json(cache_file)
    cues = parse_subtitle(subtitle)["cues"]
    reference = readable_reference(video, subtitle, len(cues)) if len(cues) >= MIN_CUES and not oversized_reason(cues) else None
    if len(cues) < MIN_CUES:
        diagnosis = {"state": "too-short", "parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": []}
    elif oversized_reason(cues):
        diagnosis = {"state": "too-large", "parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": []}
    elif wrong_end_share(cues) > MAX_WRONG_END_SHARE:
        diagnosis = {"state": "wrong-ends", "parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": []}
    elif reference is None:
        diagnosis = {"state": "no-reference", "parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": []}
    else:
        diagnosis = diagnosis_from(cues, *judge(cues, reference))
    write_json(cache_file, diagnosis)
    return diagnosis


def diagnosis_file(video, subtitle):
    key = hashlib.sha1(f"{DIAGNOSIS_CACHE_VERSION}|{subtitle}|{file_signature(subtitle)}|{video}|{file_signature(video)}".encode()).hexdigest()
    return state_path("diagnoses", key + ".json")


def diagnosis_from(cues, decision, original, weak, attempt):
    unchanged = {"parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": weak}
    if attempt is None:
        return {**unchanged, "state": "in-sync"}
    if decision == DECISION["kept"]:
        return {**unchanged, "state": "unclear" if weak else "in-sync"}
    if not attempt["fixed"]:
        return {**unchanged, "state": "no-match"}
    shifts = [new_start - start for (new_start, _, _), (start, _, _) in zip(attempt["timings"], cues, strict=False)]
    changes = describe_changes(cues, attempt)
    return {"state": "out-of-sync", "parts": changes["parts"], "scale": changes["scale"], "fixable": True, "shifts": shifts, "weak_minutes": weak}


def came_from_bazarr(video, subtitle):
    return any(attempt.get("media_id") and attempt.get("provider") for attempt in load_state(video, subtitle).get("attempts", []))


def reference_texts(video, reference):
    if reference["image"]:
        return None
    status = os.stat(video)
    cache_file = state_path("texts", hashlib.sha1(f"{video}|{status.st_size}|{int(status.st_mtime)}|{reference['index']}".encode()).hexdigest() + ".json")
    if os.path.exists(cache_file):
        return [tuple(line) for line in read_json(cache_file)]
    try:
        probe = probe_json(["ffprobe", "-v", "error", *MEDIA_INPUT_OPTIONS, "-show_entries", "stream=index,codec_type,codec_name", "-of", "json", video],
                           video).get("streams", [])
    except ToolError as error:
        log("review.reference-unreadable", f"video streams unreadable for the line list: {error}", LEVEL["warning"], video=video)
        return None
    codec = next((stream.get("codec_name") for stream in probe if stream["index"] == reference["index"]), "")
    try:
        texts = matroska_texts(video, sum(stream.get("codec_type") != "attachment" for stream in probe), reference["index"], ass=codec in ("ass", "ssa"))
    except (MatroskaError, OSError):
        texts = None
    if texts is None:
        try:
            with heavy_disk_read():
                extracted = run_on_video(["ffmpeg", "-nostdin", "-loglevel", "error", *MEDIA_INPUT_OPTIONS, "-i", video, "-map", f"0:{reference['index']}",
                                          "-f", "srt", "-"], video, EXTRACT_SECONDS)
        except ToolError as error:
            log("review.reference-unreadable", f"reference text not extracted for the line list: {error}", LEVEL["warning"], video=video)
            return None
        if extracted.returncode != 0:
            log("review.reference-unreadable", f"reference text not extracted for the line list: {stderr_tail(extracted)}", LEVEL["warning"], video=video)
            return None
        texts = parse_timed_text(extracted.stdout.replace("\r\n", "\n"))
    write_json(cache_file, texts)
    return texts


def clean(text):
    return " ".join(MARKUP.sub("", text.replace("\\N", " ")).split())
