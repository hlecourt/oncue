# SPDX-License-Identifier: GPL-3.0-or-later
"""What a person does from Review: apply a proposal, move lines, restore a saved version, delete, keep as is, check again and ask Bazarr for
another subtitle. Every write is backed up first and recorded in the history."""

import hashlib
import os

from . import configuration, jobs
from .alignment import cue_starts, score, weak_minutes
from .bazarr import REPLACEMENT_STAGE, request_another
from .catalog import discard_proposal, proposal_for
from .confidence import confidence
from .configuration import MODE
from .errors import EditRefusedError, StaleVersionError
from .files import file_hash, read_regular, remove_file, restore_version, save_backup, subtitle_lock
from .guard import evaluate_and_fix
from .heartbeats import worker_health
from .library import VIDEO_EXTENSIONS, folder_neighbours
from .logs import log
from .outcome import DECISION
from .reference import load_reference
from .reports import report
from .state import load_state, update_state
from .storage import backup_folders, file_signature
from .subtitles import is_hearing_impaired, parse_subtitle, write_subtitle

STALE_VERSION = "the subtitle changed since this page was opened, reload it and try again"
BEFORE_THE_START = "some lines would start before the beginning of the video"


def apply_proposal(video, subtitle, expected_version=None):
    version = file_hash(subtitle)
    if expected_version and expected_version != version:
        raise StaleVersionError(STALE_VERSION)
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


def shift_range(video, subtitle, first, last, offset, expected_hash=None):
    if first > last:
        raise EditRefusedError("the part ends before it starts")
    with subtitle_lock(subtitle):
        if expected_hash and file_hash(subtitle) != expected_hash:
            raise StaleVersionError(STALE_VERSION)
        document = parse_subtitle(subtitle)
        timings = [(start + offset, end + offset, text) if first <= start <= last else (start, end, text) for start, end, text in document["cues"]]
        if any(start < 0 for start, _, _ in timings):
            raise EditRefusedError(BEFORE_THE_START)
        save_backup(subtitle, "before-manual")
        write_subtitle(subtitle, document, timings)
    discard_proposal(subtitle)
    parts = [{"first": first, "last": last, "shift": round(offset, 2), "lines": sum(first <= start <= last for start, _, _ in document["cues"])}]
    outcome = manual_outcome(video, subtitle, document["cues"], DECISION["manual"], f"shifted {first:.1f}-{last:.1f}s by {offset:+.2f}s", 1.0, parts)
    log("review.shifted", f"manual shift range={first:.2f}-{last:.2f}s offset={offset:+.2f}s", subtitle=subtitle, first=first, last=last,
        offset=offset, confidence=outcome["confidence"])
    return outcome


def retime(video, subtitle, expected_version, use_proposal, changes):
    document = checked_document(subtitle, expected_version)
    cues = document["cues"]
    proposal = proposal_for(subtitle, expected_version) if use_proposal else None
    if use_proposal and (proposal is None or len(proposal["timings"]) != len(cues)):
        raise EditRefusedError("the guard's fix does not match this file any more")
    timings = retimed_timings(cues, proposal, changes)
    backup_folder = replace_version(subtitle, expected_version, "original" if proposal else "before-manual", lambda: write_subtitle(subtitle, document, timings))
    guard_changes = (proposal or {}).get("changes") or {}
    parts = guard_changes.get("parts", []) + [{"first": cues[first][0], "last": cues[last][0], "shift": round(offset, 2), "lines": last - first + 1}
                                              for first, last, offset in changes]
    decision = DECISION["approved"] if proposal and not changes else DECISION["manual"]
    reason = ("the guard's fix and " if proposal else "") + (f"{len(changes)} change(s) by hand" if changes else "the guard's fix applied by hand")
    outcome = manual_outcome(video, subtitle, cues, decision, reason, guard_changes.get("scale", 1.0), parts)
    log("review.retimed", f"retimed by hand: {reason}, backup={backup_folder}", subtitle=subtitle, backup=backup_folder, changes=len(changes),
        guard_fix=bool(proposal), confidence=outcome["confidence"])
    return outcome


def retimed_timings(cues, proposal, changes):
    offsets = [0.0] * len(cues)
    for first, last, offset in changes:
        if not 0 <= first <= last < len(cues):
            raise EditRefusedError("a change covers lines this subtitle does not have")
        for index in range(first, last + 1):
            offsets[index] += offset
    if proposal is None and not any(offsets):
        raise EditRefusedError("there is nothing to change")
    base = [tuple(timing) for timing in proposal["timings"]] if proposal else [(start, end) for start, end, _ in cues]
    timings = [(start + moved, end + moved, text) for (start, end), moved, (_, _, text) in zip(base, offsets, cues, strict=True)]
    if any(start < 0 for start, _, _ in timings):
        raise EditRefusedError(BEFORE_THE_START)
    return timings


def checked_document(subtitle, expected_version):
    content = read_regular(subtitle)
    if hashlib.sha1(content).hexdigest() != expected_version:
        raise StaleVersionError(STALE_VERSION)
    return parse_subtitle(subtitle, raw=content)


def replace_version(subtitle, expected_version, backup_label, write):
    with subtitle_lock(subtitle):
        if file_hash(subtitle) != expected_version:
            raise StaleVersionError(STALE_VERSION)
        backup_folder = save_backup(subtitle, backup_label)
        write()
    discard_proposal(subtitle)
    return backup_folder


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


def measure(cues, reference):
    original = score(cue_starts(cues), reference["starts"])
    if original is None:
        return None
    outcome = {"decision": DECISION["kept"], "after": original, "weak_minutes": weak_minutes(cues, reference)}
    trust, band = confidence(outcome)
    return {**original, "weak_minutes": outcome["weak_minutes"], "confidence": trust, "confidence_band": band}


def restore_saved_version(subtitle, saved, expected_version):
    folder_name, file_name = saved.split("/", 1) if saved.count("/") == 1 else ("", "")
    source = next((os.path.join(folder, file_name) for folder in backup_folders(subtitle) if os.path.basename(folder) == folder_name), None)
    if source is None or os.sep in file_name or not os.path.isfile(source) or os.path.islink(source):
        raise EditRefusedError("that saved version does not exist")
    replace_version(subtitle, expected_version, "before-restore", lambda: restore_version(subtitle, source))
    report(subtitle, "revert", {"decision": DECISION["reverted"], "reason": f"restored the {os.path.splitext(file_name)[0]} version of {folder_name}"})
    log("review.version-restored", f"saved version restored from {source}", subtitle=subtitle, source=source)


def delete_subtitle(subtitle, expected_version):
    backup_folder = replace_version(subtitle, expected_version, "deleted", lambda: remove_file(subtitle))
    report(subtitle, "ui", {"decision": DECISION["deleted"], "reason": f"deleted by hand, a copy is in {backup_folder}"})
    log("review.deleted", f"subtitle deleted by hand, backup={backup_folder}", subtitle=subtitle, backup=backup_folder)
    return backup_folder


def accept(subtitle):
    jobs.accept(subtitle, file_signature(subtitle))
    log("review.accepted", "accepted as is, excluded from review until the file changes", subtitle=subtitle)


def recheck(video, subtitle):
    folder, name = os.path.split(video)
    videos = sorted(entry for entry in os.listdir(folder) if entry.lower().endswith(VIDEO_EXTENSIONS))
    apply = configuration.value("mode") == MODE["fix"]
    log("review.recheck-requested", f"recheck requested from the UI apply={apply}", subtitle=subtitle, applied=apply)
    return jobs.enqueue(jobs.KIND["check"], {"video": video, "subtitle": subtitle, "apply": apply, "neighbours": folder_neighbours(folder, name, videos)})


def ask_for_another(video, subtitle):
    state = load_state(video, subtitle)
    last = next((attempt for attempt in reversed(state.get("attempts", [])) if attempt.get("media_id") and attempt.get("provider")), None)
    if last is None:
        return False
    pending = {"media_type": "series" if last.get("series_id") else "movie", "media_id": last["media_id"], "series_id": last.get("series_id"),
               "subtitle": subtitle, "provider": last["provider"], "subtitle_id": last["subtitle_id"], "language": last.get("language"),
               "content_hash": file_hash(subtitle), "stage": REPLACEMENT_STAGE["requested"]}
    update_state(video, subtitle, lambda current: current.update(pending_replacement=pending))
    request_another(video, subtitle, queued=bool(worker_health()))
    log("review.replacement-requested", f"replacement requested provider={last['provider']} id={last['subtitle_id']}", subtitle=subtitle, provider=last["provider"])
    return True
