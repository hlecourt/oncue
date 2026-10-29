# SPDX-License-Identifier: GPL-3.0-or-later
"""Review tools behind the web UI: side-by-side reading, manual shifts, revert, accept, recheck and asking for another subtitle."""

import hashlib
import os
import re
from contextlib import suppress

import numpy

from . import jobs
from .alignment import (
    MAX_WRONG_END_SHARE,
    MIN_CUES,
    NOTICEABLE_SHIFT_SECONDS,
    TOLERANCE_SECONDS,
    cue_starts,
    describe_changes,
    judge,
    nearest_gap,
    out_of_sync_parts,
    oversized_reason,
    score,
    weak_minutes,
    wrong_end_share,
)
from .bazarr import RETRY_STAGE, request_another
from .catalog import discard_proposal, proposal_for
from .confidence import confidence
from .errors import EditRefusedError, MatroskaError, StaleVersionError, ToolError
from .files import file_hash, inside_roots, remove_file, restore_version, save_backup, subtitle_lock
from .guard import evaluate_and_fix
from .heartbeats import worker_health
from .library import VIDEO_EXTENSIONS, folder_neighbours
from .logs import LEVEL, log
from .matroska import matroska_texts
from .outcome import DECISION
from .reference import load_reference, probe_json
from .reports import latest_reports, needs_review, report
from .state import load_state, update_state
from .storage import (
    backup_folders,
    file_signature,
    heavy_disk_read,
    read_json,
    revert_source,
    state_path,
    write_json,
)
from .subtitles import (
    is_hearing_impaired,
    parse_subtitle,
    parse_timed_text,
    write_subtitle,
)
from .tools import EXTRACT_SECONDS, run_tool

MARKUP = re.compile(r"\{[^}]*\}|<[^>]+>")
MIN_MINUTE_LINES = 3
BEFORE_FIX_NAMES = ("original", "before-manual")
DIAGNOSIS_CACHE_VERSION = 3


def review_items():
    accepted = jobs.accepted()
    return [row for row in latest_reports() if needs_review(row) and accepted.get(row["subtitle"]) != file_signature(row["subtitle"])]


def video_for(subtitle):
    folder, name = os.path.split(subtitle)
    candidates = [os.path.join(folder, video) for video in os.listdir(folder)
                  if video.lower().endswith(VIDEO_EXTENSIONS) and name.startswith(os.path.splitext(video)[0] + ".")]
    safe = [video for video in candidates if not os.path.islink(video) and os.path.isfile(video) and inside_roots(video)]
    return max(safe, key=len) if safe else None


def side_by_side(video, subtitle, first, last, shifts=None):
    cues = parse_subtitle(subtitle)["cues"]
    reference = readable_reference(video, subtitle, len(cues))
    if reference is None:
        return {"reference": None, "lines": [(start, "subtitle", clean(text), None, index) for index, (start, _, text) in enumerate(cues) if first <= start < last],
                "moments": pair_moments(cues, [], [start for start, _, _ in cues], first, last), "minutes": []}
    texts = reference_texts(video, reference) or [(start, start, "") for start in reference["starts"]]
    starts = cue_starts(cues)
    matched = numpy.abs(nearest_gap(starts, reference["starts"])) <= TOLERANCE_SECONDS if len(starts) else numpy.array([], dtype=bool)
    lines = [(start, "reference", clean(text), None, None) for start, _, text in texts if first <= start < last]
    lines += [(start, "subtitle", clean(text), bool(hit), index) for index, ((start, _, text), hit) in enumerate(zip(cues, matched, strict=False)) if first <= start < last]
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
    reference_starts = cue_starts(texts)
    moments = {position: {"time": start, "video": clean(text), "subtitle": None, "shown_at": None, "shift": 0.0}
               for position, (start, _, text) in enumerate(texts) if first <= start < last}
    for index, ((start, _, text), target) in enumerate(zip(cues, targets, strict=False)):
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
    shifts = [new_start - old_start for (new_start, _, _), (old_start, _, _) in zip(new, old, strict=False)]
    if len(old) != len(new) or max((abs(shift) for shift in shifts), default=0) < NOTICEABLE_SHIFT_SECONDS:
        return None
    moved_minutes = sorted({int(start // 60) for (start, _, _), shift in zip(new, shifts, strict=False) if abs(shift) >= NOTICEABLE_SHIFT_SECONDS})
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


def known_diagnosis(video, subtitle):
    cache_file = diagnosis_file(video, subtitle)
    return read_json(cache_file) if os.path.exists(cache_file) else None


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


def apply_proposal(video, subtitle, expected_version=None):
    version = file_hash(subtitle)
    if expected_version and expected_version != version:
        raise StaleVersionError("the subtitle changed since this page was opened, reload it and try again")
    proposal = proposal_for(subtitle, version)
    if proposal is None:
        evaluate_and_fix(video, subtitle, False)
        proposal = proposal_for(subtitle, version)
    if proposal is None:
        return None
    with subtitle_lock(subtitle):
        if file_hash(subtitle) != version:
            raise StaleVersionError("the subtitle changed while the proposal was applied, nothing was written")
        document = parse_subtitle(subtitle)
        if len(document["cues"]) != len(proposal["timings"]):
            raise EditRefusedError("the proposal does not match this file any more, nothing was written")
        timings = [(start, end, text) for (start, end), (_, _, text) in zip(proposal["timings"], document["cues"], strict=True)]
        backup_folder = save_backup(subtitle, "original")
        write_subtitle(subtitle, document, timings)
    discard_proposal(subtitle)
    outcome = {"decision": DECISION["approved"], "reason": f"{proposal['kind']} proposal applied by hand", "changes": proposal.get("changes"),
               "confidence": proposal.get("confidence"), "confidence_band": proposal.get("confidence_band"), "reference": proposal.get("reference") or ""}
    report(subtitle, "ui", outcome)
    log("review.proposal-applied", f"{proposal['kind']} proposal applied, backup={backup_folder}", subtitle=subtitle, backup=backup_folder,
        confidence=proposal.get("confidence"))
    return outcome


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
        probe = probe_json(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries", "stream=index,codec_type,codec_name", "-of", "json", video],
                           video).get("streams", [])
    except ToolError:
        return None
    codec = next((stream.get("codec_name") for stream in probe if stream["index"] == reference["index"]), "")
    try:
        texts = matroska_texts(video, sum(stream.get("codec_type") != "attachment" for stream in probe), reference["index"], ass=codec in ("ass", "ssa"))
    except (MatroskaError, OSError):
        texts = None
    if texts is None:
        try:
            with heavy_disk_read():
                extracted = run_tool(["ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file", "-i", video, "-map", f"0:{reference['index']}",
                                      "-f", "srt", "-"], EXTRACT_SECONDS)
        except ToolError:
            return None
        if extracted.returncode != 0:
            return None
        texts = parse_timed_text(extracted.stdout.replace("\r\n", "\n"))
    write_json(cache_file, texts)
    return texts


def measure(cues, reference):
    original = score(cue_starts(cues), reference["starts"])
    if original is None:
        return None
    outcome = {"decision": DECISION["kept"], "after": original, "weak_minutes": weak_minutes(cues, reference)}
    trust, band = confidence(outcome)
    return {**original, "weak_minutes": outcome["weak_minutes"], "confidence": trust, "confidence_band": band}


def shift_range(video, subtitle, first, last, offset, expected_hash=None):
    if first > last:
        raise EditRefusedError("the part ends before it starts")
    with subtitle_lock(subtitle):
        if expected_hash and file_hash(subtitle) != expected_hash:
            raise StaleVersionError("the subtitle changed since this page was opened, reload it and try again")
        document = parse_subtitle(subtitle)
        timings = [(start + offset, end + offset, text) if first <= start <= last else (start, end, text) for start, end, text in document["cues"]]
        if any(start < 0 for start, _, _ in timings):
            raise EditRefusedError("some lines would start before the beginning of the video")
        save_backup(subtitle, "before-manual")
        write_subtitle(subtitle, document, timings)
    discard_proposal(subtitle)
    parts = [{"first": first, "last": last, "shift": round(offset, 2), "lines": sum(first <= start <= last for start, _, _ in document["cues"])}]
    outcome = manual_outcome(video, subtitle, document["cues"], DECISION["manual"], f"shifted {first:.1f}-{last:.1f}s by {offset:+.2f}s", 1.0, parts)
    log("review.shifted", f"manual shift range={first:.2f}-{last:.2f}s offset={offset:+.2f}s", subtitle=subtitle, first=first, last=last,
        offset=offset, confidence=outcome["confidence"])
    return outcome


def retime(video, subtitle, expected_version, use_proposal, changes):
    with subtitle_lock(subtitle):
        if file_hash(subtitle) != expected_version:
            raise StaleVersionError("the subtitle changed since this page was opened, reload it and try again")
        document = parse_subtitle(subtitle)
        cues = document["cues"]
        proposal = proposal_for(subtitle, expected_version) if use_proposal else None
        if use_proposal and (proposal is None or len(proposal["timings"]) != len(cues)):
            raise EditRefusedError("the guard's fix does not match this file any more")
        offsets = [0.0] * len(cues)
        for first, last, offset in changes:
            if not 0 <= first <= last < len(cues):
                raise EditRefusedError("a change covers lines this subtitle does not have")
            for index in range(first, last + 1):
                offsets[index] += offset
        if not use_proposal and not any(offsets):
            raise EditRefusedError("there is nothing to change")
        base = [tuple(timing) for timing in proposal["timings"]] if proposal else [(start, end) for start, end, _ in cues]
        timings = [(start + moved, end + moved, text) for (start, end), moved, (_, _, text) in zip(base, offsets, cues, strict=True)]
        if any(start < 0 for start, _, _ in timings):
            raise EditRefusedError("some lines would start before the beginning of the video")
        backup_folder = save_backup(subtitle, "original" if proposal else "before-manual")
        write_subtitle(subtitle, document, timings)
    discard_proposal(subtitle)
    guard_changes = ((proposal or {}).get("changes") or {})
    parts = guard_changes.get("parts", []) + [{"first": cues[first][0], "last": cues[last][0], "shift": round(offset, 2), "lines": last - first + 1}
                                              for first, last, offset in changes]
    decision = DECISION["approved"] if proposal and not changes else DECISION["manual"]
    reason = ("the guard's fix and " if proposal else "") + (f"{len(changes)} change(s) by hand" if changes else "the guard's fix applied by hand")
    outcome = manual_outcome(video, subtitle, cues, decision, reason, guard_changes.get("scale", 1.0), parts)
    log("review.retimed", f"retimed by hand: {reason}, backup={backup_folder}", subtitle=subtitle, backup=backup_folder, changes=len(changes),
        guard_fix=bool(proposal), confidence=outcome["confidence"])
    return outcome


def manual_outcome(video, subtitle, previous_cues, decision, reason, scale, parts):
    cues = parse_subtitle(subtitle)["cues"]
    reference = load_reference(video, is_hearing_impaired(subtitle), len(cues))
    previous = measure(previous_cues, reference) if reference else None
    measured = measure(cues, reference) if reference else None
    outcome = {"decision": decision, "reference": reference["kind"] if reference else "", "before": previous or {}, "after": measured or {},
               "weak_minutes": (measured or {}).get("weak_minutes", []), "confidence": (measured or {}).get("confidence", 0),
               "confidence_band": (measured or {}).get("confidence_band", "none"), "reason": reason,
               "changes": {"scale": scale, "lines": len(cues), "parts": parts}}
    report(subtitle, "manual", outcome)
    return outcome


def revert(subtitle):
    original = revert_source(subtitle)
    if original is None:
        return False
    with subtitle_lock(subtitle):
        save_backup(subtitle, "before-revert")
        restore_version(subtitle, original)
    discard_proposal(subtitle)
    report(subtitle, "revert", {"decision": DECISION["reverted"], "reason": f"restored the original from {os.path.basename(os.path.dirname(original))}"})
    log("review.reverted", f"original restored from {original}", subtitle=subtitle, original=original)
    return True


def restore_saved_version(subtitle, saved, expected_version):
    folder_name, file_name = saved.split("/", 1) if saved.count("/") == 1 else ("", "")
    source = next((os.path.join(folder, file_name) for folder in backup_folders(subtitle) if os.path.basename(folder) == folder_name), None)
    if source is None or os.sep in file_name or not os.path.isfile(source) or os.path.islink(source):
        raise EditRefusedError("that saved version does not exist")
    with subtitle_lock(subtitle):
        if file_hash(subtitle) != expected_version:
            raise StaleVersionError("the subtitle changed since this page was opened, reload it and try again")
        save_backup(subtitle, "before-restore")
        restore_version(subtitle, source)
    discard_proposal(subtitle)
    report(subtitle, "revert", {"decision": DECISION["reverted"], "reason": f"restored the {os.path.splitext(file_name)[0]} version of {folder_name}"})
    log("review.version-restored", f"saved version restored from {source}", subtitle=subtitle, source=source)


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


def delete_subtitle(subtitle):
    with subtitle_lock(subtitle):
        backup_folder = save_backup(subtitle, "deleted")
        remove_file(subtitle)
    discard_proposal(subtitle)
    report(subtitle, "ui", {"decision": DECISION["deleted"], "reason": f"deleted by hand, a copy is in {backup_folder}"})
    log("review.deleted", f"subtitle deleted by hand, backup={backup_folder}", subtitle=subtitle, backup=backup_folder)
    return backup_folder


def ask_for_another(video, subtitle):
    state = load_state(video, subtitle)
    last = next((attempt for attempt in reversed(state.get("attempts", [])) if attempt.get("media_id") and attempt.get("provider")), None)
    if last is None:
        return False
    pending = {"media_type": "series" if last.get("series_id") else "movie", "media_id": last["media_id"], "series_id": last.get("series_id"),
               "subtitle": subtitle, "provider": last["provider"], "subtitle_id": last["subtitle_id"], "language": last.get("language"),
               "content_hash": file_hash(subtitle), "stage": RETRY_STAGE["requested"]}
    update_state(video, subtitle, lambda current: current.update(pending_retry=pending))
    request_another(video, subtitle, queued=bool(worker_health()))
    log("review.replacement-requested", f"replacement requested provider={last['provider']} id={last['subtitle_id']}", subtitle=subtitle, provider=last["provider"])
    return True


def clean(text):
    return " ".join(MARKUP.sub("", text.replace("\\N", " ")).split())
