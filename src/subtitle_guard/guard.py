"""Check one subtitle against its video and repair it when the result can be trusted."""

import os
import shutil

from .alignment import MIN_CUES, assess, judge
from .audio import sync_to_audio
from .confidence import confidence
from .outcome import DECISION
from .reference import load_reference
from .storage import backup_path
from .subtitles import HEARING_IMPAIRED_TAGS, parse_subtitle, write_subtitle


def evaluate_and_fix(video, subtitle, apply, neighbours=()):
    outcome = examine(video, subtitle, apply, neighbours)
    score, band = confidence(outcome)
    return {**outcome, "confidence": score, "confidence_band": band}


def examine(video, subtitle, apply, neighbours):
    if ".forced." in os.path.basename(subtitle).lower():
        return {"decision": DECISION["unverified"], "reason": "forced subtitle, too few lines to check"}
    document = parse_subtitle(subtitle)
    if len(document["cues"]) < MIN_CUES:
        return {"decision": DECISION["unverified"], "reason": f"too few cues ({len(document['cues'])})"}
    hearing_impaired = any(tag in os.path.basename(subtitle).lower() for tag in HEARING_IMPAIRED_TAGS)
    reference = load_reference(video, hearing_impaired, len(document["cues"]))
    if reference is None and not apply:
        return {"decision": DECISION["unverified"], "reason": "no usable subtitle track embedded in the video, would sync to the audio"}
    if reference is None:
        return sync_to_audio(video, subtitle)
    outcome = summarize(reference["kind"], *judge(document["cues"], reference))
    if outcome["decision"] == DECISION["kept"]:
        return outcome
    attempt = outcome.pop("attempt")
    if not attempt["fixed"]:
        better = find_better_episode(document["cues"], hearing_impaired, neighbours)
        if better:
            outcome = {**outcome, "decision": DECISION["wrong_episode"], "matches": better}
    if not apply:
        return outcome
    backup_folder = backup_path(subtitle)
    extension = os.path.splitext(subtitle)[1]
    shutil.copy2(subtitle, os.path.join(backup_folder, "original" + extension))
    if attempt["fixed"]:
        write_subtitle(subtitle, document, attempt["timings"])
    best_copy = os.path.join(backup_folder, "kept" + extension)
    shutil.copy2(subtitle, best_copy)
    return {**outcome, "best_copy": best_copy}


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
        if reference and assess(cues, reference)["fixed"]:
            return label
    return None
