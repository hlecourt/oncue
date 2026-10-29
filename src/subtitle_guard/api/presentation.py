# SPDX-License-Identifier: GPL-3.0-or-later
"""The plain words the front shows for each decision, made here so the front and the CLI say the same thing."""

import os

from ..outcome import DECISION, DECISION_WORDS
from .i18n import tr

TONE = {"good": "good", "warn": "warn", "bad": "bad", "neutral": "neutral"}
DECISION_TONES = {"kept": "good", "fixed": "good", "manual": "good", "approved": "good", "rejected": "bad", "wrong-episode": "bad", "best-effort": "warn",
                  "unverified": "neutral", "audio-synced": "warn", "reverted": "neutral", "proposed": "warn", "deleted": "neutral"}
DRY_RUN_WORDS = {"fixed": "A sure fix was found, not written (dry run)", "audio-synced": "An audio sync was found, not written (dry run)",
                 "best-effort": "A best version was found, not written (dry run)"}
UNSURE_WORDS = {"kept": "Looks in sync, not sure", "fixed": "Fixed, not sure", "manual": "Adjusted by hand, still off in places"}
BAND_WORDS = {"high": "High confidence", "medium": "Medium confidence", "low": "Low confidence", "none": "No confidence"}
STARTED_BY = {"hook": "Bazarr", "scan": "Library scan", "watch": "Folder watch", "check": "A check you started", "manual": "You, by hand", "ui": "You, in the UI",
              "revert": "You, restore"}
HISTORY_GROUPS = {"fixed": ("Fixed", ("fixed", "approved")), "proposals": ("Proposals", ("proposed", "dry-run")), "in-sync": ("In sync", ("kept",)),
                  "not-fixed": ("Not fixed", ("rejected", "wrong-episode", "best-effort")), "not-checked": ("Cannot be checked", ("unverified", "audio-synced")),
                  "by-hand": ("By hand", ("manual", "reverted", "deleted"))}
UNCHANGED_DECISIONS = ("kept", "rejected", "wrong-episode", "unverified", "proposed", "deleted")
CHANGES_SHOWN = 3
SPEED_TOLERANCE = 0.001


def result_words(row):
    if row["decision"] == DECISION["wrong_episode"] and row["note"]:
        return tr("Belongs to {episode}", episode=row["note"]), TONE["bad"]
    words = tr(DECISION_WORDS.get(row["decision"], row["decision"]))
    if not row.get("applied", 1) and row["decision"] in DRY_RUN_WORDS:
        return tr(DRY_RUN_WORDS[row["decision"]]), TONE["neutral"]
    if (row["band"] == "low" or row["weak_minutes"]) and row["decision"] in UNSURE_WORDS:
        return tr(UNSURE_WORDS[row["decision"]]), TONE["warn"]
    return words, DECISION_TONES.get(row["decision"], TONE["neutral"])


def review_reason(row, proposal):
    if proposal:
        return tr("A fix from the sound is ready") if proposal["kind"] == "audio" else tr("A fix from the video's subtitles is ready")
    if row["decision"] == DECISION["wrong_episode"] and row["note"]:
        return tr("Belongs to {episode}", episode=row["note"])
    if row["weak_minutes"]:
        return tr("Out of sync around {time}", time=clock(int(row["weak_minutes"].split(",")[0]) * 60))
    if row["decision"] == DECISION["rejected"]:
        return tr("Does not match the video")
    return tr(DECISION_WORDS.get(row["decision"], row["decision"]))


def review_tone(row, proposal):
    if proposal:
        return TONE["warn"]
    if row["decision"] in (DECISION["rejected"], DECISION["wrong_episode"]) or row["band"] == "low":
        return TONE["bad"]
    if row["decision"] in (DECISION["unverified"], DECISION["audio_synced"], DECISION["reverted"]):
        return TONE["neutral"]
    return TONE["warn"]


def started_by(provider):
    if not provider:
        return ""
    if provider in STARTED_BY:
        return tr(STARTED_BY[provider])
    return tr("Bazarr (downloaded from {provider})", provider=provider)


def change_sentences(changes, decision):
    if not changes and decision in UNCHANGED_DECISIONS:
        return [tr("Nothing moved")]
    if not changes:
        return [tr("Not recorded")]
    sentences = [tr("Speed corrected: it was {speed}", speed=speed_label(changes["scale"]))] if speed_changed(changes.get("scale", 1.0)) else []
    for part in changes.get("parts", [])[:CHANGES_SHOWN]:
        sentences.append(part_sentence(part, changes["lines"]))
    if len(changes.get("parts", [])) > CHANGES_SHOWN:
        sentences.append(tr("and {count} more parts", count=len(changes["parts"]) - CHANGES_SHOWN))
    return sentences or [tr("Small timing touch-ups")]


def part_sentence(part, total):
    values = {"seconds": f"{abs(part['shift']):.1f}", "count": part["lines"], "first": clock(part["first"]), "last": clock(part["last"])}
    earlier = part["shift"] < 0
    if part["lines"] >= 0.9 * total:
        values["count"] = total
        return tr("All {count} lines moved {seconds} s earlier", **values) if earlier else tr("All {count} lines moved {seconds} s later", **values)
    if earlier:
        return tr("{count} lines from {first} to {last} moved {seconds} s earlier", **values)
    return tr("{count} lines from {first} to {last} moved {seconds} s later", **values)


def speed_changed(scale):
    return abs(scale - 1) > SPEED_TOLERANCE


def speed_label(scale):
    percent = f"{abs(scale - 1):.1%}"
    if scale > 1:
        return tr("made for a faster version of the video ({percent})", percent=percent)
    return tr("made for a slower version of the video ({percent})", percent=percent)


def clock(value):
    hours, rest = divmod(int(value), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def folder_label(path):
    folder = os.path.dirname(path)
    return " / ".join(part for part in (os.path.basename(os.path.dirname(folder)), os.path.basename(folder)) if part)
