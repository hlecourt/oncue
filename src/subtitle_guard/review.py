"""Review tools behind the web UI: side-by-side reading, manual shifts, revert, accept, recheck and asking for another subtitle."""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess

import numpy

from . import jobs
from .alignment import TOLERANCE_SECONDS, nearest_gap, score, weak_minutes
from .bazarr import request_another
from .confidence import confidence
from .library import VIDEO_EXTENSIONS, folder_neighbours
from .logs import log
from .matroska import matroska_texts
from .outcome import DECISION
from .reference import load_reference
from .storage import backup_path, backup_root, file_signature, heavy_disk_read, latest_reports, load_state, needs_review, report, save_state, state_path, write_json
from .subtitles import HEARING_IMPAIRED_TAGS, parse_subtitle, parse_timed_text, write_subtitle

MARKUP = re.compile(r"\{[^}]*\}|<[^>]+>")


def review_items():
    accepted = jobs.accepted()
    return [row for row in latest_reports() if needs_review(row) and accepted.get(row["subtitle"]) != file_signature(row["subtitle"])]


def video_for(subtitle):
    folder, name = os.path.split(subtitle)
    candidates = [video for video in os.listdir(folder) if video.lower().endswith(VIDEO_EXTENSIONS) and name.startswith(os.path.splitext(video)[0] + ".")]
    return os.path.join(folder, max(candidates, key=len)) if candidates else None


def side_by_side(video, subtitle, first, last):
    cues = parse_subtitle(subtitle)["cues"]
    reference = load_reference(video, is_hearing_impaired(subtitle), len(cues))
    if reference is None:
        return {"reference": None, "lines": [(start, "subtitle", clean(text), None) for start, _, text in cues if first <= start < last], "minutes": []}
    texts = reference_texts(video, reference) or [(start, start, "") for start in reference["starts"]]
    starts = numpy.array([start for start, _, _ in cues])
    matched = numpy.abs(nearest_gap(starts, reference["starts"])) <= TOLERANCE_SECONDS if len(starts) else numpy.array([], dtype=bool)
    lines = [(start, "reference", clean(text), None) for start, _, text in texts if first <= start < last]
    lines += [(start, "subtitle", clean(text), bool(hit)) for (start, _, text), hit in zip(cues, matched) if first <= start < last]
    minutes = [(minute, float(matched[(starts >= minute * 60) & (starts < minute * 60 + 60)].mean()))
               for minute in range(int(starts.max() // 60) + 1) if ((starts >= minute * 60) & (starts < minute * 60 + 60)).sum() >= 3] if len(starts) else []
    return {"reference": reference["kind"], "lines": sorted(lines, key=lambda line: (line[0], line[1] == "subtitle")), "minutes": minutes,
            "measure": measure(cues, reference)}


def reference_texts(video, reference):
    if reference["image"]:
        return None
    status = os.stat(video)
    cache_file = state_path("texts", hashlib.sha1(f"{video}|{status.st_size}|{int(status.st_mtime)}|{reference['index']}".encode()).hexdigest() + ".json")
    if os.path.exists(cache_file):
        return [tuple(line) for line in json.load(open(cache_file))]
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name", "-of", "json", video],
                                      capture_output=True, text=True).stdout or "{}").get("streams", [])
    codec = next((stream.get("codec_name") for stream in probe if stream["index"] == reference["index"]), "")
    try:
        texts = matroska_texts(video, sum(stream.get("codec_type") != "attachment" for stream in probe), reference["index"], ass=codec in ("ass", "ssa"))
    except (ValueError, IndexError, KeyError, StopIteration, OSError):
        texts = None
    if texts is None:
        with heavy_disk_read():
            extracted = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", video, "-map", f"0:{reference['index']}", "-f", "srt", "-"],
                                       capture_output=True, text=True, errors="replace").stdout
        texts = parse_timed_text(extracted.replace("\r\n", "\n"))
    write_json(cache_file, texts)
    return texts


def measure(cues, reference):
    original = score(numpy.array([start for start, _, _ in cues]), reference["starts"])
    if original is None:
        return None
    outcome = {"decision": DECISION["kept"], "after": original, "weak_minutes": weak_minutes(cues, reference)}
    trust, band = confidence(outcome)
    return {**original, "weak_minutes": outcome["weak_minutes"], "confidence": trust, "confidence_band": band}


def shift_range(video, subtitle, first, last, offset):
    keep_current(subtitle, "before-manual")
    document = parse_subtitle(subtitle)
    timings = [(start + offset, end + offset, text) if first <= start <= last else (start, end, text) for start, end, text in document["cues"]]
    write_subtitle(subtitle, document, timings)
    cues = parse_subtitle(subtitle)["cues"]
    reference = load_reference(video, is_hearing_impaired(subtitle), len(cues))
    measured = measure(cues, reference) if reference else None
    outcome = {"decision": DECISION["manual"], "reference": reference["kind"] if reference else "", "before": measured or {}, "after": measured or {},
               "weak_minutes": (measured or {}).get("weak_minutes", []), "confidence": (measured or {}).get("confidence", 0),
               "confidence_band": (measured or {}).get("confidence_band", "none"), "reason": f"shifted {first:.1f}-{last:.1f}s by {offset:+.2f}s"}
    report(subtitle, "manual", outcome)
    log("manual-shift", subtitle=subtitle, first=first, last=last, offset=offset, confidence=outcome["confidence"])
    return outcome


def revert(subtitle):
    originals = sorted(glob.glob(os.path.join(glob.escape(backup_root(subtitle)), "*", "original.*")))
    if not originals:
        return False
    keep_current(subtitle, "before-revert")
    shutil.copy2(originals[0], subtitle)
    report(subtitle, "revert", {"decision": DECISION["reverted"], "reason": f"restored the original from {os.path.basename(os.path.dirname(originals[0]))}"})
    log("reverted", subtitle=subtitle, original=originals[0])
    return True


def accept(subtitle):
    jobs.accept(subtitle, file_signature(subtitle))
    log("accepted", subtitle=subtitle)


def recheck(video, subtitle):
    folder, name = os.path.split(video)
    videos = sorted(entry for entry in os.listdir(folder) if entry.lower().endswith(VIDEO_EXTENSIONS))
    return jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle, "apply": True, "neighbours": folder_neighbours(folder, name, videos)})


def ask_for_another(video, subtitle):
    state = load_state(video, subtitle)
    last = next((attempt for attempt in reversed(state.get("attempts", [])) if attempt.get("media_id") and attempt.get("provider")), None)
    if last is None:
        return False
    state["pending_retry"] = {"media_type": "series" if last.get("series_id") else "movie", "media_id": last["media_id"], "series_id": last.get("series_id"),
                              "subtitle": subtitle, "provider": last["provider"], "subtitle_id": last["subtitle_id"], "language": last.get("language")}
    save_state(video, subtitle, state)
    request_another(video, subtitle)
    log("another-requested", subtitle=subtitle, provider=last["provider"])
    return True


def keep_current(subtitle, label):
    folder = backup_path(subtitle)
    shutil.copy2(subtitle, os.path.join(folder, label + os.path.splitext(subtitle)[1]))


def is_hearing_impaired(subtitle):
    return any(tag in os.path.basename(subtitle).lower() for tag in HEARING_IMPAIRED_TAGS)


def clean(text):
    return " ".join(MARKUP.sub("", text.replace("\\N", " ")).split())
