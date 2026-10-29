# SPDX-License-Identifier: GPL-3.0-or-later
"""Check one subtitle against its video, repair it on its own only at high confidence, and keep any other fix as a proposal."""

import os
import time

from . import configuration
from .alignment import (
    MAX_CUE_SECONDS,
    MAX_WRONG_END_SHARE,
    MIN_CUES,
    assess,
    describe_changes,
    judge,
    oversized_reason,
    wrong_end_share,
)
from .audio import audio_fix
from .catalog import discard_proposal, save_proposal
from .confidence import confidence
from .errors import SubtitleTooLargeError, ToolError, UnsafePathError
from .files import file_hash, save_backup, subtitle_lock
from .logs import LEVEL, log
from .outcome import CHANGED_REASON, DECISION, FIX_KIND, WORRYING_DECISIONS
from .reference import load_reference
from .storage import file_signature
from .subtitles import is_hearing_impaired, parse_subtitle, write_subtitle

AUTOMATIC_BAND = "high"
AUDIO_REFERENCE = "audio"


def evaluate_and_fix(video, subtitle, apply, neighbours=()):
    started = time.time()
    outcome, fix = examine(video, subtitle, apply, neighbours)
    score, band = confidence(outcome)
    outcome = settle(video, subtitle, {**outcome, "confidence": score, "confidence_band": band}, fix, apply)
    outcome["seconds"] = round(time.time() - started, 2)
    message = (f"decision={outcome['decision']} confidence={score} band={band} reference={outcome.get('reference') or '-'} in {outcome['seconds']:.2f}s"
               + (f" matches={outcome['matches']}" if outcome.get("matches") else "") + ("" if apply else " dry-run"))
    level = LEVEL["warning"] if outcome["decision"] in WORRYING_DECISIONS else LEVEL["info"]
    log("guard.checked", message, level, video=video, subtitle=subtitle, applied=apply, decision=outcome["decision"], confidence=score, band=band,
        reference=outcome.get("reference"), reason=outcome.get("reason"), matches=outcome.get("matches"),
        weak_minutes=outcome.get("weak_minutes"), before=outcome.get("before"), after=outcome.get("after"), objective=outcome.get("objective"),
        null_objective=outcome.get("null_objective"), segments=outcome.get("segments"), unsupported_share=outcome.get("unsupported_share"),
        changes=outcome.get("changes"), seconds=outcome["seconds"])
    return outcome


def settle(video, subtitle, outcome, fix, apply):
    if fix is None:
        return outcome
    automatic = outcome["confidence_band"] == AUTOMATIC_BAND and (fix["kind"] != FIX_KIND["audio"] or configuration.value("audio_automatic"))
    if not (automatic and apply):
        save_proposal(subtitle, video, fix["version"], fix["kind"], fix["timings"], outcome)
    if not automatic:
        log("guard.proposed", f"{fix['kind']} fix kept as a proposal, confidence {outcome['confidence']}", subtitle=subtitle,
            confidence=outcome["confidence"], kind=fix["kind"])
        return {**outcome, "decision": DECISION["proposed"], "proposed": outcome["decision"]}
    if not apply:
        return outcome
    with subtitle_lock(subtitle):
        if file_signature(subtitle) != fix["signature"]:
            log("guard.changed-during-check", "subtitle changed on disk during the check, write skipped", LEVEL["warning"], subtitle=subtitle)
            return {"decision": DECISION["unverified"], "reason": CHANGED_REASON}
        backup_folder = save_backup(subtitle, "original")
        write_subtitle(subtitle, fix["document"], fix["timings"])
    discard_proposal(subtitle)
    log("guard.rewritten", f"subtitle rewritten with aligned timings, backup={backup_folder}", subtitle=subtitle, backup=backup_folder)
    return {**outcome, "backup": backup_folder}


def examine(video, subtitle, apply, neighbours):
    if ".forced." in os.path.basename(subtitle).lower():
        return {"decision": DECISION["unverified"], "reason": "forced subtitle, too few lines to check"}, None
    signature = file_signature(subtitle)
    try:
        version = file_hash(subtitle)
        document = parse_subtitle(subtitle)
    except (UnsafePathError, SubtitleTooLargeError) as error:
        return {"decision": DECISION["unverified"], "reason": f"refused: {error}"}, None
    if len(document["cues"]) < MIN_CUES:
        return {"decision": DECISION["unverified"], "reason": f"too few cues ({len(document['cues'])})"}, None
    oversized = oversized_reason(document["cues"])
    if oversized:
        return {"decision": DECISION["unverified"], "reason": oversized}, None
    wrong_ends = wrong_end_share(document["cues"])
    if wrong_ends > MAX_WRONG_END_SHARE:
        return {"decision": DECISION["rejected"], "reason": f"the end times are wrong: {wrong_ends:.0%} of lines end before they start or last over "
                                                            f"{MAX_CUE_SECONDS} s"}, None
    hearing_impaired = is_hearing_impaired(subtitle)
    try:
        reference = load_reference(video, hearing_impaired, len(document["cues"]))
    except ToolError as error:
        return {"decision": DECISION["unverified"], "reason": f"could not read the video, nothing changed: {error}"}, None
    fix = {"document": document, "signature": signature, "version": version}
    if reference is None and not apply:
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, would sync to the audio"}, None
    if reference is None:
        outcome, timings = audio_fix(video, subtitle, document)
        return {**outcome, "reference": AUDIO_REFERENCE}, ({**fix, "kind": FIX_KIND["audio"], "timings": timings} if timings else None)
    outcome = summarize(reference["kind"], *judge(document["cues"], reference))
    if outcome["decision"] == DECISION["kept"]:
        return outcome, None
    attempt = outcome.pop("attempt")
    if attempt["fixed"]:
        outcome["changes"] = describe_changes(document["cues"], attempt)
        return outcome, {**fix, "kind": FIX_KIND["aligned"], "timings": attempt["timings"]}
    better = find_better_episode(document["cues"], hearing_impaired, neighbours)
    if better:
        outcome = {**outcome, "decision": DECISION["wrong_episode"], "matches": better}
    return outcome, None


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
        try:
            reference = load_reference(video, hearing_impaired, len(cues))
        except (ToolError, OSError) as error:
            log("guard.neighbour-skipped", f"neighbour {label} unreadable, skipped: {error}", LEVEL["warning"], video=video)
            continue
        if reference and assess(cues, reference, wide_retry=False)["fixed"]:
            return label
    return None
