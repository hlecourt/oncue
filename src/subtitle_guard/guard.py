# SPDX-License-Identifier: GPL-3.0-or-later
"""Check one subtitle against its video and repair it when the result can be trusted."""

import os
import time

from .alignment import (
    MAX_CUE_SECONDS,
    MAX_WRONG_END_SHARE,
    MIN_CUES,
    assess,
    describe_changes,
    judge,
    wrong_end_share,
)
from .audio import sync_to_audio
from .confidence import confidence
from .errors import SubtitleTooLargeError, ToolError, UnsafePathError
from .files import save_version, subtitle_lock
from .logs import LEVEL, log
from .outcome import DECISION, WORRYING_DECISIONS
from .reference import load_reference
from .storage import backup_path, file_signature
from .subtitles import is_hearing_impaired, parse_subtitle, write_subtitle

MAX_CHECKED_CUES = 20000
CHANGED_REASON = "the subtitle changed during the check, it was left as it is and will be checked again"


def evaluate_and_fix(video, subtitle, apply, neighbours=()):
    started = time.time()
    with subtitle_lock(subtitle):
        outcome = examine(video, subtitle, apply, neighbours)
    score, band = confidence(outcome)
    outcome = {**outcome, "confidence": score, "confidence_band": band}
    message = (f"decision={outcome['decision']} confidence={score} band={band} reference={outcome.get('reference') or '-'} in {time.time() - started:.2f}s"
               + (f" matches={outcome['matches']}" if outcome.get("matches") else "") + ("" if apply else " dry-run"))
    level = LEVEL["warning"] if outcome["decision"] in WORRYING_DECISIONS else LEVEL["info"]
    log("guard.checked", message, level, video=video, subtitle=subtitle, applied=apply, decision=outcome["decision"], confidence=score, band=band,
        reference=outcome.get("reference"), reason=outcome.get("reason"), matches=outcome.get("matches"),
        weak_minutes=outcome.get("weak_minutes"), before=outcome.get("before"), after=outcome.get("after"), objective=outcome.get("objective"),
        null_objective=outcome.get("null_objective"), segments=outcome.get("segments"), unsupported_share=outcome.get("unsupported_share"),
        changes=outcome.get("changes"), seconds=round(time.time() - started, 2))
    return outcome


def examine(video, subtitle, apply, neighbours):
    if ".forced." in os.path.basename(subtitle).lower():
        return {"decision": DECISION["unverified"], "reason": "forced subtitle, too few lines to check"}
    signature = file_signature(subtitle)
    try:
        document = parse_subtitle(subtitle)
    except (UnsafePathError, SubtitleTooLargeError) as error:
        return {"decision": DECISION["unverified"], "reason": f"refused: {error}"}
    if len(document["cues"]) < MIN_CUES:
        return {"decision": DECISION["unverified"], "reason": f"too few cues ({len(document['cues'])})"}
    if len(document["cues"]) > MAX_CHECKED_CUES:
        return {"decision": DECISION["unverified"], "reason": f"too many cues to check safely ({len(document['cues'])})"}
    wrong_ends = wrong_end_share(document["cues"])
    if wrong_ends > MAX_WRONG_END_SHARE:
        return {"decision": DECISION["rejected"], "reason": f"the end times are wrong: {wrong_ends:.0%} of lines end before they start or last over "
                                                            f"{MAX_CUE_SECONDS} s"}
    hearing_impaired = is_hearing_impaired(subtitle)
    try:
        reference = load_reference(video, hearing_impaired, len(document["cues"]))
    except ToolError as error:
        return {"decision": DECISION["unverified"], "reason": f"could not read the video, nothing changed: {error}"}
    if reference is None and not apply:
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, would sync to the audio"}
    if reference is None:
        return sync_to_audio(video, subtitle, signature)
    outcome = summarize(reference["kind"], *judge(document["cues"], reference))
    if outcome["decision"] == DECISION["kept"]:
        return outcome
    attempt = outcome.pop("attempt")
    if attempt["fixed"]:
        outcome["changes"] = describe_changes(document["cues"], attempt)
    if not attempt["fixed"]:
        better = find_better_episode(document["cues"], hearing_impaired, neighbours)
        if better:
            outcome = {**outcome, "decision": DECISION["wrong_episode"], "matches": better}
    if not apply:
        return outcome
    if file_signature(subtitle) != signature:
        log("guard.changed-during-check", "subtitle changed on disk during the check, write skipped", LEVEL["warning"], subtitle=subtitle)
        return {"decision": DECISION["unverified"], "reason": CHANGED_REASON}
    backup_folder = backup_path(subtitle)
    save_version(subtitle, backup_folder, "original")
    if attempt["fixed"]:
        write_subtitle(subtitle, document, attempt["timings"])
        log("guard.rewritten", f"subtitle rewritten with aligned timings, backup={backup_folder}", subtitle=subtitle, backup=backup_folder)
    return {**outcome, "best_copy": save_version(subtitle, backup_folder, "kept")}


def summarize(reference_kind, decision, original, weak, attempt):
    outcome = {"reference": reference_kind, "before": original}
    if decision == DECISION["kept"]:
        return {**outcome, "decision": decision, "after": original, "weak_minutes": weak}
    return {**outcome, "after": attempt["score"] if attempt["fixed"] else original, "aligned_attempt": attempt["score"],
            "objective": round(attempt["objective"], 3), "null_objective": round(attempt["null_objective"], 3),
            "segments": attempt["segments"], "unsupported_share": round(attempt["unsupported_share"], 2), "weak_minutes": attempt["weak_minutes"],
            "decision": DECISION["fixed"] if attempt["fixed"] else DECISION["rejected"], "attempt": attempt}


def find_better_episode(cues, hearing_impaired, neighbours):
    for label, video in neighbours:
        reference = load_reference(video, hearing_impaired, len(cues))
        if reference and assess(cues, reference, wide_retry=False)["fixed"]:
            return label
    return None
