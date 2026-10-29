# SPDX-License-Identifier: GPL-3.0-or-later
"""Decisions the guard can reach and their one-line description."""

DECISION = {"kept": "kept", "fixed": "fixed", "rejected": "rejected", "unverified": "unverified", "wrong_episode": "wrong-episode",
            "best_effort": "best-effort", "audio_synced": "audio-synced", "manual": "manual", "reverted": "reverted"}
SETTLED_DECISIONS = (DECISION["kept"], DECISION["fixed"], DECISION["manual"])
DECISION_WORDS = {"kept": "In sync", "fixed": "Fixed automatically", "rejected": "Could not be fixed", "wrong-episode": "Belongs to another episode",
                  "unverified": "Cannot be checked", "audio-synced": "Synced to the audio, not checked", "best-effort": "Best version kept, not fully in sync",
                  "manual": "Adjusted by hand", "reverted": "Back to the original file"}
CHANGED_REASON = "the subtitle changed during the check, it was left as it is and will be checked again"
WORRYING_DECISIONS = (DECISION["rejected"], DECISION["wrong_episode"], DECISION["best_effort"])


def describe(outcome):
    if outcome["decision"] in (DECISION["unverified"], DECISION["reverted"]):
        return f"subtitle-guard: {outcome['decision']} ({outcome['reason']})"
    if outcome["decision"] == DECISION["audio_synced"]:
        return f"subtitle-guard: {outcome['decision']} ({outcome['reason']}) | confidence {outcome.get('confidence', 0)} ({outcome.get('confidence_band', 'low')})"
    before, after = outcome["before"], outcome["after"]
    details = [f"subtitle-guard: {outcome['decision']}", f"reference {outcome['reference']}", f"noise {before['noise']:.2f}",
               f"matched {before['matched']:.2f} -> {after['matched']:.2f}", f"worst 2-min window {before['worst_window']:.2f} -> {after['worst_window']:.2f}"]
    if outcome.get("aligned_attempt") and outcome["decision"] != DECISION["fixed"]:
        details.append(f"aligned attempt {outcome['aligned_attempt']['matched']:.2f}/{outcome['aligned_attempt']['worst_window']:.2f}")
    if outcome.get("objective") is not None:
        details.append(f"objective {outcome['objective']:.2f} vs null {outcome['null_objective']:.2f}")
    if outcome.get("segments"):
        details.append(f"{outcome['segments']} segments")
    if outcome.get("unsupported_share"):
        details.append(f"{outcome['unsupported_share']:.0%} of lines in unsupported segments")
    if outcome.get("weak_minutes"):
        details.append("weak minutes " + ",".join(str(minute) for minute in outcome["weak_minutes"]))
    if outcome.get("matches"):
        details.append(f"matches {outcome['matches']}")
    if "confidence" in outcome:
        details.append(f"confidence {outcome['confidence']} ({outcome['confidence_band']})")
    return " | ".join(details)
