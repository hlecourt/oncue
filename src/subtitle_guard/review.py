"""Review tools behind the web UI: side-by-side reading, manual shifts, revert, accept, recheck and asking for another subtitle."""

import glob
import hashlib
import json
import os
import re
import shutil

import numpy

from . import jobs
from .errors import MatroskaError, ToolError, UnsafePathError
from .tools import EXTRACT_SECONDS, PROBE_SECONDS, run_tool
from .alignment import MAX_WRONG_END_SHARE, MIN_CUES, wrong_end_share, NOTICEABLE_SHIFT_SECONDS, TOLERANCE_SECONDS, describe_changes, judge, nearest_gap, out_of_sync_parts, score, weak_minutes
from .bazarr import request_another
from .worker import worker_health
from .confidence import confidence
from .guard import evaluate_and_fix
from .library import VIDEO_EXTENSIONS, folder_neighbours
from .logs import LEVEL, log
from .matroska import matroska_texts
from .outcome import DECISION
from .reference import load_reference
from .reports import latest_reports, needs_review, report
from .storage import backup_folders, backup_path, file_signature, heavy_disk_read, load_state, revert_source, save_state, state_path, write_json
from .files import restore_version, save_version, subtitle_lock
from .subtitles import is_hearing_impaired, parse_subtitle, parse_timed_text, write_subtitle

MARKUP = re.compile(r"\{[^}]*\}|<[^>]+>")
MIN_MINUTE_LINES = 3
BEFORE_FIX_NAMES = ("original", "before-manual")
DIAGNOSIS_CACHE_VERSION = 3


def review_items():
    accepted = jobs.accepted()
    return [row for row in latest_reports() if needs_review(row) and accepted.get(row["subtitle"]) != file_signature(row["subtitle"])]


def video_for(subtitle):
    folder, name = os.path.split(subtitle)
    candidates = [video for video in os.listdir(folder) if video.lower().endswith(VIDEO_EXTENSIONS) and name.startswith(os.path.splitext(video)[0] + ".")]
    return os.path.join(folder, max(candidates, key=len)) if candidates else None


def side_by_side(video, subtitle, first, last, shifts=None):
    cues = parse_subtitle(subtitle)["cues"]
    reference = readable_reference(video, subtitle, len(cues))
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
        if inside.sum() < MIN_MINUTE_LINES:
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
        line = {"subtitle": clean(text), "shown_at": start, "shift": float(target - start), "index": index}
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


def last_fix(video, subtitle):
    extension = os.path.splitext(subtitle)[1]
    candidates = [os.path.join(folder, name + extension) for folder in reversed(backup_folders(subtitle)) for name in BEFORE_FIX_NAMES]
    before = next((path for path in candidates if os.path.exists(path)), None)
    if before is None:
        return None
    old, new = parse_subtitle(before)["cues"], parse_subtitle(subtitle)["cues"]
    shifts = [new_start - old_start for (new_start, _, _), (old_start, _, _) in zip(new, old)]
    if len(old) != len(new) or max((abs(shift) for shift in shifts), default=0) < NOTICEABLE_SHIFT_SECONDS:
        return None
    moved_minutes = sorted({int(start // 60) for (start, _, _), shift in zip(new, shifts) if abs(shift) >= NOTICEABLE_SHIFT_SECONDS})
    return {"time": os.path.basename(os.path.dirname(before)), "shifts": shifts, "moved_minutes": moved_minutes, "parts": out_of_sync_parts(old, shifts)}


def readable_reference(video, subtitle, cue_count):
    try:
        return load_reference(video, is_hearing_impaired(subtitle), cue_count)
    except ToolError as error:
        log("review.reference-unreadable", f"video unreadable for the review page: {error}", LEVEL["warning"], video=video, subtitle=subtitle)
        return None


def diagnose(video, subtitle):
    cache_file = diagnosis_file(video, subtitle)
    if os.path.exists(cache_file):
        return json.load(open(cache_file))
    cues = parse_subtitle(subtitle)["cues"]
    reference = readable_reference(video, subtitle, len(cues)) if len(cues) >= MIN_CUES else None
    if len(cues) < MIN_CUES:
        diagnosis = {"state": "too-short", "parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": []}
    elif wrong_end_share(cues) > MAX_WRONG_END_SHARE:
        diagnosis = {"state": "wrong-ends", "parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": []}
    elif reference is None:
        diagnosis = {"state": "no-reference", "parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": []}
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
    unchanged = {"parts": [], "scale": 1.0, "fixable": False, "shifts": [], "weak_minutes": weak}
    if attempt is None:
        return {**unchanged, "state": "in-sync"}
    if decision == DECISION["kept"]:
        return {**unchanged, "state": "unclear" if weak else "in-sync"}
    if not attempt["fixed"]:
        return {**unchanged, "state": "no-match"}
    shifts = [new_start - start for (new_start, _, _), (start, _, _) in zip(attempt["timings"], cues)]
    changes = describe_changes(cues, attempt)
    return {"state": "out-of-sync", "parts": changes["parts"], "scale": changes["scale"], "fixable": True, "shifts": shifts, "weak_minutes": weak}


def fix_everything(video, subtitle):
    log("review.fix-requested", "full fix requested from the UI", subtitle=subtitle)
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
    probe = json.loads(run_tool(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries", "stream=index,codec_type,codec_name", "-of", "json",
                                 video], PROBE_SECONDS).stdout or "{}").get("streams", [])
    codec = next((stream.get("codec_name") for stream in probe if stream["index"] == reference["index"]), "")
    try:
        texts = matroska_texts(video, sum(stream.get("codec_type") != "attachment" for stream in probe), reference["index"], ass=codec in ("ass", "ssa"))
    except (MatroskaError, OSError):
        texts = None
    if texts is None:
        with heavy_disk_read():
            extracted = run_tool(["ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file", "-i", video, "-map", f"0:{reference['index']}",
                                  "-f", "srt", "-"], EXTRACT_SECONDS).stdout
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
    with subtitle_lock(subtitle):
        document = parse_subtitle(subtitle)
        save_version(subtitle, backup_path(subtitle), "before-manual")
        timings = [(start + offset, end + offset, text) if first <= start <= last else (start, end, text) for start, end, text in document["cues"]]
        write_subtitle(subtitle, document, timings)
    cues = parse_subtitle(subtitle)["cues"]
    reference = load_reference(video, is_hearing_impaired(subtitle), len(cues))
    previous = measure(document["cues"], reference) if reference else None
    measured = measure(cues, reference) if reference else None
    outcome = {"decision": DECISION["manual"], "reference": reference["kind"] if reference else "", "before": previous or {}, "after": measured or {},
               "weak_minutes": (measured or {}).get("weak_minutes", []), "confidence": (measured or {}).get("confidence", 0),
               "confidence_band": (measured or {}).get("confidence_band", "none"), "reason": f"shifted {first:.1f}-{last:.1f}s by {offset:+.2f}s",
               "changes": {"scale": 1.0, "lines": len(cues), "parts": [{"first": first, "last": last, "shift": round(offset, 2),
                                                                         "lines": sum(first <= start <= last for start, _, _ in document["cues"])}]}}
    report(subtitle, "manual", outcome)
    log("review.shifted", f"manual shift range={first:.2f}-{last:.2f}s offset={offset:+.2f}s", subtitle=subtitle, first=first, last=last,
        offset=offset, confidence=outcome["confidence"])
    return outcome


def revert(subtitle):
    original = revert_source(subtitle)
    if original is None:
        return False
    with subtitle_lock(subtitle):
        save_version(subtitle, backup_path(subtitle), "before-revert")
        restore_version(subtitle, original)
    report(subtitle, "revert", {"decision": DECISION["reverted"], "reason": f"restored the original from {os.path.basename(os.path.dirname(original))}"})
    log("review.reverted", f"original restored from {original}", subtitle=subtitle, original=original)
    return True


def has_backup(subtitle):
    return revert_source(subtitle) is not None


def accept(subtitle):
    jobs.accept(subtitle, file_signature(subtitle))
    log("review.accepted", "accepted as is, excluded from review until the file changes", subtitle=subtitle)


def recheck(video, subtitle):
    folder, name = os.path.split(video)
    videos = sorted(entry for entry in os.listdir(folder) if entry.lower().endswith(VIDEO_EXTENSIONS))
    log("review.recheck-requested", "recheck requested from the UI", subtitle=subtitle)
    return jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle, "apply": True, "neighbours": folder_neighbours(folder, name, videos)})


def move_to_episode(video, subtitle, label):
    folder, name = os.path.split(video)
    videos = sorted(entry for entry in os.listdir(folder) if entry.lower().endswith(VIDEO_EXTENSIONS))
    target_video = next((path for neighbour, path in folder_neighbours(folder, name, videos) if neighbour == label), None)
    if target_video is None:
        return None
    suffix = os.path.basename(subtitle)[len(os.path.splitext(name)[0]):]
    target = os.path.splitext(target_video)[0] + suffix
    if os.path.islink(subtitle) or os.path.islink(target):
        raise UnsafePathError(f"refusing to move a symlinked subtitle: {subtitle}")
    with subtitle_lock(subtitle), subtitle_lock(target):
        if os.path.exists(target):
            save_version(target, backup_path(target), "before-move")
        save_version(subtitle, backup_path(subtitle), "moved-away")
        os.replace(subtitle, target)
    log("review.moved", f"moved to {label} target={target}", subtitle=subtitle, target=target, episode=label)
    return target, recheck(target_video, target)


def ask_for_another(video, subtitle):
    state = load_state(video, subtitle)
    last = next((attempt for attempt in reversed(state.get("attempts", [])) if attempt.get("media_id") and attempt.get("provider")), None)
    if last is None:
        return False
    state["pending_retry"] = {"media_type": "series" if last.get("series_id") else "movie", "media_id": last["media_id"], "series_id": last.get("series_id"),
                              "subtitle": subtitle, "provider": last["provider"], "subtitle_id": last["subtitle_id"], "language": last.get("language")}
    save_state(video, subtitle, state)
    request_another(video, subtitle, queued=bool(worker_health()))
    log("review.replacement-requested", f"replacement requested provider={last['provider']} id={last['subtitle_id']}", subtitle=subtitle, provider=last["provider"])
    return True


def clean(text):
    return " ".join(MARKUP.sub("", text.replace("\\N", " ")).split())
