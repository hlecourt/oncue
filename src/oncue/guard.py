# SPDX-License-Identifier: GPL-3.0-or-later
"""Check one subtitle against its video, repair it on its own only at high confidence, and keep any other fix as a proposal."""

import os
import time

from . import configuration, seam_audio, seam_speech, speech_check, text_check
from .alignment import (
    MAX_CUE_SECONDS,
    MAX_WRONG_END_SHARE,
    MIN_CUES,
    assess,
    describe_changes,
    judge,
    out_of_sync_parts,
    oversized_reason,
    score,
    wrong_end_share,
)
from .audio import audio_fix
from .catalog import discard_proposal, save_proposal
from .confidence import BANDS, TEXT_UNSETTLED_CEILING, confidence
from .errors import ConfigError, SubtitleTooLargeError, ToolError, UnsafePathError
from .files import confined, file_hash, save_backup, subtitle_lock
from .logs import LEVEL, log
from .outcome import CHANGED_REASON, DECISION, FIX_KIND, WORRYING_DECISIONS
from .reference import load_reference
from .reference_text import reference_texts
from .storage import file_signature
from .subtitles import is_hearing_impaired, output_flaw, parse_subtitle, write_subtitle
from .text_check import every_minute
from .text_model import load_model

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
        if outcome.get("withheld"):
            # a proposal vetoed on this version takes down the one an earlier check saved
            discard_proposal(subtitle)
        return outcome
    flaw = timing_flaw(fix["document"], fix["timings"])
    if flaw:
        outcome = {**outcome, "reason": f"not written automatically: {flaw}"}
    automatic = (outcome["confidence_band"] == AUTOMATIC_BAND and not flaw
                 and (fix["kind"] != FIX_KIND["audio"] or configuration.value("audio_automatic")))
    if not (automatic and apply):
        save_proposal(subtitle, video, fix["version"], fix["kind"], fix["timings"], outcome)
    if not automatic:
        log("guard.proposed", f"{fix['kind']} fix kept as a proposal, confidence {outcome['confidence']}", subtitle=subtitle,
            confidence=outcome["confidence"], kind=fix["kind"])
        return {**outcome, "decision": DECISION["proposed"], "proposed": outcome["decision"]}
    if not apply:
        return outcome
    try:
        confined(subtitle)
    except UnsafePathError as error:
        log("guard.write-refused", str(error), LEVEL["warning"], subtitle=subtitle)
        return {**outcome, "decision": DECISION["unverified"], "reason": f"not written: {error}"}
    with subtitle_lock(subtitle):
        if file_signature(subtitle) != fix["signature"]:
            log("guard.changed-during-check", "subtitle changed on disk during the check, write skipped", LEVEL["warning"], subtitle=subtitle)
            return {"decision": DECISION["unverified"], "reason": CHANGED_REASON}
        backup_folder = save_backup(subtitle, "original")
        write_subtitle(subtitle, fix["document"], fix["timings"])
        remaining = fix.get("remaining")
        written_version = file_hash(subtitle) if remaining else None
    discard_proposal(subtitle)
    log("guard.rewritten", f"subtitle rewritten with aligned timings, backup={backup_folder}", subtitle=subtitle, backup=backup_folder)
    if remaining and not timing_flaw(fix["document"], remaining):
        # a partial write: the unvouched rest waits in Review on the written version, scored as a proposal, not with the written part's confidence
        shifts = [new[0] - old[0] for new, old in zip(remaining, fix["timings"], strict=True)]
        capped = min(outcome["confidence"], TEXT_UNSETTLED_CEILING)
        details = {**outcome, "confidence": capped, "confidence_band": next(band for floor, band in BANDS if capped >= floor),
                   "changes": {"scale": 1.0, "parts": out_of_sync_parts(fix["timings"], shifts), "lines": len(remaining)}}
        save_proposal(subtitle, video, written_version, fix["kind"], remaining, details)
        log("guard.proposed", "remaining text proposal kept for review on the written version", subtitle=subtitle, kind=fix["kind"])
    return {**outcome, "backup": backup_folder}


def timing_flaw(document, timings=None):
    # checked as they will be written, before any backup: refused output stays a proposal instead of failing mid-write; called with the
    # timings alone (no file to render), only what the timings themselves show is checked
    if timings is None:
        timings, document = document, None
    if wrong_end_share(timings) > MAX_WRONG_END_SHARE:
        return "too many lines would end wrongly"
    if document is None:
        return next((reason for reason, broken in (("a line would start before zero", any(start < 0 for start, _, _ in timings)),
                                                   ("a line would end before it starts", any(end < start for start, end, _ in timings))) if broken), "")
    return output_flaw(document, timings)


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
    outcome, found = decide(document["cues"], reference, text_verdict(video, reference, document["cues"], document))
    if found:
        return outcome, {**fix, **found}
    if outcome["decision"] == DECISION["kept"]:
        return outcome, None
    better = find_better_episode(document["cues"], hearing_impaired, neighbours)
    if better:
        outcome = {**outcome, "decision": DECISION["wrong_episode"], "matches": better}
    return outcome, None


def decide(cues, reference, text=None):
    """The decision without any IO, shared by examine() and the benchmarks: the timing judge, then the text verdict when the check ran
    (it has the last word), and the fix that stands, if any ({"kind", "timings"[, "remaining"]}). Confidence, the output check and the
    write itself come after, in evaluate_and_fix() and settle()."""
    outcome = summarize(reference["kind"], *judge(cues, reference))
    attempt = outcome.pop("attempt", None)
    if text:
        outcome, text_fix = with_text(outcome, text, cues, reference, {})
        if text_fix and text["verdict"] != text_check.VERDICT["corrected"]:
            return vetted(outcome, text_fix, text, cues, FIX_KIND["text"])
        if text_fix:
            return vetted_remaining(outcome, text_fix, text)
        if outcome["decision"] == DECISION["kept"]:
            return outcome, None
    if outcome["decision"] == DECISION["kept"] or not attempt["fixed"]:
        return outcome, None
    timings = text_check.led_timings(text, cues, attempt["timings"]) if text else attempt["timings"]
    if timings is not attempt["timings"]:
        # described and scored as it will be offered, after the correction for the reference's lead
        outcome["after"] = score(timings, reference)
    outcome["changes"] = describe_changes(cues, {**attempt, "timings": timings})
    fix = {"kind": FIX_KIND["aligned"], "timings": timings}
    return vetted(outcome, fix, text, cues, FIX_KIND["aligned"]) if text else (outcome, fix)


def vetted(outcome, fix, text, cues, kind):
    """A proposal the text or the speech says would make the file worse if accepted is withheld: nothing is offered and the file goes to
    Review as it is, with why, as a file that could not be fixed (not as one in sync), so the check for another episode still runs."""
    evidence = text_check.proposal_evidence(text, cues, fix["timings"], kind)
    outcome = {**outcome, "proposal_evidence": evidence}
    doubt = text_check.proposal_doubt(evidence)
    if not doubt:
        return outcome, fix
    weak = sorted(set(outcome.get("weak_minutes") or []) | set(text.get("doubtful_minutes") or []) or set(every_minute(cues)))
    rest = {key: value for key, value in outcome.items() if key != "changes"}
    return {**rest, "decision": DECISION["rejected"], "withheld": True, "after": outcome["before"], "weak_minutes": weak,
            "reason": "; ".join(part for part in (outcome.get("reason"), f"proposal withheld: {doubt}") if part)}, None


def vetted_remaining(outcome, fix, text):
    """What a partial write leaves as a proposal is vetted the same way, against the timings written."""
    if not fix.get("remaining"):
        return outcome, fix
    evidence = text_check.proposal_evidence(text, fix["timings"], fix["remaining"], FIX_KIND["text"])
    outcome = {**outcome, "remaining_evidence": evidence}
    doubt = text_check.proposal_doubt(evidence)
    if not doubt:
        return outcome, fix
    return ({**outcome, "reason": "; ".join(part for part in (outcome.get("reason"), f"remaining proposal withheld: {doubt}") if part)},
            {**fix, "remaining": None})


def text_verdict(video, reference, cues, document=None):
    # once switched on, the check fails closed: no reference text or no model gives an unsettled verdict, never a timing-only pass
    if not configuration.value("text_check"):
        return None
    texts = reference_texts(video, reference, read_pictures=True)
    if not texts:
        return text_check.unavailable("no reference text", cues)
    try:
        model = load_model(configuration.value("text_model"))
    except ConfigError as error:
        log("guard.text-check-unavailable", f"text check could not run: {error}", LEVEL["warning"], video=video)
        return text_check.unavailable("text model unavailable", cues)
    language = reference.get("kind", "").split("-")[0]
    speech_around = seam_audio.provider(video, language) if configuration.value("text_seam_audio") else None
    listen = seam_speech.listener(video, model.encode, language) if configuration.value("text_seam_speech") else None
    speech_check_on = bool(configuration.value("text_speech_check"))
    onsets = speech_check.read_onsets(video, language) if speech_check_on else None
    return text_check.decide(cues, texts, model.encode, speech_around=speech_around, family=model.family, listen=listen,
                             output_check=(lambda timings: timing_flaw(document, timings)) if document else None, speech_check_on=speech_check_on, onsets=onsets)


def with_text(outcome, text, cues, reference, fix):
    """The text decides when it is on. confirmed: the file is verified as it is; corrected: only the passages judged safe are written, and
    whatever the text could not vouch for stays in Review (weak minutes); unsettled: nothing is written, the text's proposal stands in
    only when timing has none, and every doubtful minute goes to Review. Untranslated stretches are listed in Review with their minutes."""
    summary = {key: value for key, value in text.items() if key not in ("timings", "proposal", "candidates", "file_features", "vetting")}
    outcome = {**outcome, "text": summary, "gaps": [[gap["from"], gap["to"], gap["lines"]] for gap in text.get("gaps", [])]}
    gaps_note = untranslated_note(text.get("gaps", []))
    if gaps_note:
        outcome["reason"] = "; ".join(part for part in (outcome.get("reason"), gaps_note) if part)
    doubtful = set(text.get("doubtful_minutes", []))
    if text["verdict"] == text_check.VERDICT["confirmed"]:
        return {**outcome, "decision": DECISION["kept"], "after": outcome["before"], "weak_minutes": sorted(doubtful)}, None
    proposal = text["timings"] if text["verdict"] == text_check.VERDICT["corrected"] else text.get("proposal")
    if proposal and (text["verdict"] == text_check.VERDICT["corrected"] or outcome["decision"] != DECISION["fixed"]):
        shifts = [new_start - start for (new_start, _, _), (start, _, _) in zip(proposal, cues, strict=True)]
        changes = {"scale": text.get("scale", 1.0), "parts": out_of_sync_parts(cues, shifts), "lines": len(cues)}
        weak = sorted(doubtful | set(text["off_minutes"]) if not text.get("file_verified") else doubtful)
        remaining = text.get("proposal") if text["verdict"] == text_check.VERDICT["corrected"] else None
        if remaining is not None and all(abs(left[0] - right[0]) < 1e-6 and abs(left[1] - right[1]) < 1e-6 for left, right in zip(remaining, proposal, strict=True)):
            remaining = None
        return ({**outcome, "decision": DECISION["fixed"], "after": score(proposal, reference), "changes": changes, "weak_minutes": weak},
                {**fix, "kind": FIX_KIND["text"], "timings": proposal, "remaining": remaining})
    weak = set(outcome.get("weak_minutes") or []) | set(text["off_minutes"]) | doubtful
    return {**outcome, "weak_minutes": sorted(weak or every_minute(cues)), "reason": outcome.get("reason") or text.get("reason", "")}, None


def untranslated_note(gaps):
    if not gaps:
        return ""
    parts = [f"{int(gap['from'] // 60)}:{int(gap['from'] % 60):02d}-{int(gap['to'] // 60)}:{int(gap['to'] % 60):02d} ({gap['lines']} lines)" for gap in gaps]
    return f"reference dialogue without subtitle: {', '.join(parts)}"


def summarize(reference_kind, decision, original, weak, attempt):
    outcome = {"reference": reference_kind, "before": original}
    if decision == DECISION["kept"]:
        return {**outcome, "decision": decision, "after": original, "weak_minutes": weak}
    return {**outcome, "after": attempt["score"] if attempt["fixed"] else original, "aligned_attempt": attempt["score"],
            "objective": round(attempt["objective"], 3), "null_objective": round(attempt["null_objective"], 3),
            "segments": attempt["segments"], "unsupported_share": round(attempt["unsupported_share"], 2), "strength": round(attempt["strength"], 2),
            "weak_minutes": attempt["weak_minutes"],
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
