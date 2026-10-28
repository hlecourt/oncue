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
from .alignment import MIN_CUES, TOLERANCE_SECONDS, judge, nearest_gap, score, weak_minutes
from .bazarr import request_another
from .confidence import confidence
from .guard import evaluate_and_fix
from .library import VIDEO_EXTENSIONS, folder_neighbours
from .logs import log
from .matroska import matroska_texts
from .outcome import DECISION
from .reference import load_reference
from .storage import backup_path, backup_root, file_signature, heavy_disk_read, latest_reports, load_state, needs_review, report, save_state, state_path, write_json
from .subtitles import HEARING_IMPAIRED_TAGS, parse_subtitle, parse_timed_text, write_subtitle

MARKUP = re.compile(r"\{[^}]*\}|<[^>]+>")
NOTICEABLE_SHIFT_SECONDS = 0.5
SAME_PART_SECONDS = 0.6
MIN_PART_LINES = 3
DIAGNOSIS_CACHE_VERSION = 1


def review_items():
    accepted = jobs.accepted()
    return [row for row in latest_reports() if needs_review(row) and accepted.get(row["subtitle"]) != file_signature(row["subtitle"])]


def video_for(subtitle):
    folder, name = os.path.split(subtitle)
    candidates = [video for video in os.listdir(folder) if video.lower().endswith(VIDEO_EXTENSIONS) and name.startswith(os.path.splitext(video)[0] + ".")]
    return os.path.join(folder, max(candidates, key=len)) if candidates else None


def side_by_side(video, subtitle, first, last, shifts=None):
    cues = parse_subtitle(subtitle)["cues"]
    reference = load_reference(video, is_hearing_impaired(subtitle), len(cues))
    if reference is None:
        return {"reference": None, "lines": [(start, "subtitle", clean(text), None, index) for index, (start, _, text) in enumerate(cues) if first <= start < last],
                "moments": pair_moments(cues, [], [start for start, _, _ in cues], first, last), "minutes": []}
    texts = reference_texts(video, reference) or [(start, start, "") for start in reference["starts"]]
    starts = numpy.array([start for start, _, _ in cues])
    matched = numpy.abs(nearest_gap(starts, reference["starts"])) <= TOLERANCE_SECONDS if len(starts) else numpy.array([], dtype=bool)
    lines = [(start, "reference", clean(text), None, None) for start, _, text in texts if first <= start < last]
    lines += [(start, "subtitle", clean(text), bool(hit), index) for index, ((start, _, text), hit) in enumerate(zip(cues, matched)) if first <= start < last]
    shifted = bool(shifts) and len(shifts) == len(cues)
    targets = starts + numpy.array(shifts) if shifted else starts
    on_time = numpy.abs(targets - starts) < NOTICEABLE_SHIFT_SECONDS if shifted else matched
    return {"reference": reference["kind"], "lines": sorted(lines, key=lambda line: (line[0], line[1] == "subtitle")), "minutes": minute_states(starts, on_time, shifted),
            "moments": pair_moments(cues, texts, targets, first, last), "measure": measure(cues, reference)}


def minute_states(starts, on_time, shifted):
    states = []
    for minute in range(int(starts.max() // 60) + 1 if len(starts) else 0):
        inside = (starts >= minute * 60) & (starts < minute * 60 + 60)
        if inside.sum() < MIN_PART_LINES:
            states.append((minute, None, "quiet"))
            continue
        share = float(on_time[inside].mean())
        states.append((minute, share, minute_state(share, shifted)))
    return states


def minute_state(share, shifted):
    good, bad = (1.0, 0.5) if shifted else (0.55, 0.35)
    if share >= good:
        return "good"
    return "bad" if share < bad else "medium"


def pair_moments(cues, texts, targets, first, last):
    reference_starts = numpy.array([start for start, _, _ in texts])
    moments = {position: {"time": start, "video": clean(text), "subtitle": None, "shown_at": None, "shift": 0.0}
               for position, (start, _, text) in enumerate(texts) if first <= start < last}
    for index, ((start, _, text), target) in enumerate(zip(cues, targets)):
        if not first <= target < last:
            continue
        line = {"subtitle": clean(text), "shown_at": start, "shift": float(target - start)}
        position = nearest_position(reference_starts, target)
        if position in moments and moments[position]["subtitle"] is None and abs(reference_starts[position] - target) <= TOLERANCE_SECONDS:
            moments[position].update(line)
            continue
        moments[("subtitle", index)] = {"time": float(target), "video": None, **line}
    return sorted(moments.values(), key=lambda moment: moment["time"])


def nearest_position(starts, value):
    if not len(starts):
        return None
    position = int(numpy.searchsorted(starts, value))
    candidates = [candidate for candidate in (position - 1, position) if 0 <= candidate < len(starts)]
    return min(candidates, key=lambda candidate: abs(starts[candidate] - value))


def diagnose(video, subtitle):
    cache_file = diagnosis_file(video, subtitle)
    if os.path.exists(cache_file):
        return json.load(open(cache_file))
    cues = parse_subtitle(subtitle)["cues"]
    reference = load_reference(video, is_hearing_impaired(subtitle), len(cues)) if len(cues) >= MIN_CUES else None
    if len(cues) < MIN_CUES:
        diagnosis = {"state": "too-short", "parts": [], "fixable": False, "shifts": [], "weak_minutes": []}
    elif reference is None:
        diagnosis = {"state": "no-reference", "parts": [], "fixable": False, "shifts": [], "weak_minutes": []}
    else:
        diagnosis = diagnosis_from(cues, *judge(cues, reference))
    write_json(cache_file, diagnosis)
    return diagnosis


def known_diagnosis(video, subtitle):
    cache_file = diagnosis_file(video, subtitle)
    return json.load(open(cache_file)) if os.path.exists(cache_file) else None


def diagnosis_file(video, subtitle):
    key = hashlib.sha1(f"{DIAGNOSIS_CACHE_VERSION}|{subtitle}|{file_signature(subtitle)}|{video}|{file_signature(video)}".encode()).hexdigest()
    return state_path("diagnoses", key + ".json")


def diagnosis_from(cues, decision, original, weak, attempt):
    if attempt is None:
        return {"state": "in-sync", "parts": [], "fixable": False, "shifts": [], "weak_minutes": weak}
    if decision == DECISION["kept"]:
        return {"state": "unclear" if weak else "in-sync", "parts": [], "fixable": False, "shifts": [], "weak_minutes": weak}
    if not attempt["fixed"]:
        return {"state": "no-match", "parts": [], "fixable": False, "shifts": [], "weak_minutes": weak}
    shifts = [new_start - start for (new_start, _, _), (start, _, _) in zip(attempt["timings"], cues)]
    return {"state": "out-of-sync", "parts": out_of_sync_parts(cues, shifts), "fixable": True, "shifts": shifts, "weak_minutes": weak}


def out_of_sync_parts(cues, shifts):
    parts, current = [], []
    for index in sorted(range(len(cues)), key=lambda position: cues[position][0]):
        if abs(shifts[index]) < NOTICEABLE_SHIFT_SECONDS:
            parts, current = close_part(parts, current, cues, shifts), []
            continue
        if current and abs(shifts[index] - numpy.median([shifts[member] for member in current])) > SAME_PART_SECONDS:
            parts, current = close_part(parts, current, cues, shifts), []
        current.append(index)
    return close_part(parts, current, cues, shifts)


def close_part(parts, members, cues, shifts):
    if len(members) < MIN_PART_LINES:
        return parts
    return parts + [{"first": cues[members[0]][0], "last": cues[members[-1]][0], "lines": len(members),
                     "shift": round(float(numpy.median([shifts[member] for member in members])), 1)}]


def fix_everything(video, subtitle):
    log("review.fix-everything", "Asked by hand to fix the whole subtitle", subtitle=subtitle)
    outcome = evaluate_and_fix(video, subtitle, True)
    report(subtitle, "ui", outcome)
    return outcome


def came_from_bazarr(video, subtitle):
    return any(attempt.get("media_id") and attempt.get("provider") for attempt in load_state(video, subtitle).get("attempts", []))


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
    log("review.shifted", f"Moved the lines from {first:.1f} s to {last:.1f} s by {offset:+.2f} s by hand", subtitle=subtitle, first=first, last=last,
        offset=offset, confidence=outcome["confidence"])
    return outcome


def revert(subtitle):
    originals = sorted(glob.glob(os.path.join(glob.escape(backup_root(subtitle)), "*", "original.*")))
    if not originals:
        return False
    keep_current(subtitle, "before-revert")
    shutil.copy2(originals[0], subtitle)
    report(subtitle, "revert", {"decision": DECISION["reverted"], "reason": f"restored the original from {os.path.basename(os.path.dirname(originals[0]))}"})
    log("review.reverted", "Put the original file back", subtitle=subtitle, original=originals[0])
    return True


def accept(subtitle):
    jobs.accept(subtitle, file_signature(subtitle))
    log("review.accepted", "Kept as it is by hand, it leaves the review list", subtitle=subtitle)


def recheck(video, subtitle):
    folder, name = os.path.split(video)
    videos = sorted(entry for entry in os.listdir(folder) if entry.lower().endswith(VIDEO_EXTENSIONS))
    log("review.recheck", "Asked by hand for a new check", subtitle=subtitle)
    return jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle, "apply": True, "neighbours": folder_neighbours(folder, name, videos)})


def move_to_episode(video, subtitle, label):
    folder, name = os.path.split(video)
    videos = sorted(entry for entry in os.listdir(folder) if entry.lower().endswith(VIDEO_EXTENSIONS))
    target_video = next((path for neighbour, path in folder_neighbours(folder, name, videos) if neighbour == label), None)
    if target_video is None:
        return None
    suffix = os.path.basename(subtitle)[len(os.path.splitext(name)[0]):]
    target = os.path.splitext(target_video)[0] + suffix
    if os.path.exists(target):
        keep_current(target, "before-move")
    keep_current(subtitle, "moved-away")
    os.replace(subtitle, target)
    log("review.moved", f"Moved to {label}", subtitle=subtitle, target=target, episode=label)
    return target, recheck(target_video, target)


def ask_for_another(video, subtitle):
    state = load_state(video, subtitle)
    last = next((attempt for attempt in reversed(state.get("attempts", [])) if attempt.get("media_id") and attempt.get("provider")), None)
    if last is None:
        return False
    state["pending_retry"] = {"media_type": "series" if last.get("series_id") else "movie", "media_id": last["media_id"], "series_id": last.get("series_id"),
                              "subtitle": subtitle, "provider": last["provider"], "subtitle_id": last["subtitle_id"], "language": last.get("language")}
    save_state(video, subtitle, state)
    request_another(video, subtitle)
    log("review.asked-another", f"Asked Bazarr for another subtitle instead of the {last['provider']} one", subtitle=subtitle, provider=last["provider"])
    return True


def keep_current(subtitle, label):
    folder = backup_path(subtitle)
    shutil.copy2(subtitle, os.path.join(folder, label + os.path.splitext(subtitle)[1]))


def is_hearing_impaired(subtitle):
    return any(tag in os.path.basename(subtitle).lower() for tag in HEARING_IMPAIRED_TAGS)


def clean(text):
    return " ".join(MARKUP.sub("", text.replace("\\N", " ")).split())
