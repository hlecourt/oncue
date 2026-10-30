# SPDX-License-Identifier: GPL-3.0-or-later
"""Calibrate audio sync confidence on a real library: for videos whose reference track gives the truth, shift a copy of the subtitle by a known
amount, sync it to the audio as if there were no track, and count confident results that are wrong. Nothing in the library is written."""

import json
import os
import tempfile

import numpy

from .alignment import MIN_CUES, judge
from .audio import AUDIO_SYNC_FORMATS, audio_fix
from .confidence import confidence
from .errors import OnCueError
from .guard import AUTOMATIC_BAND, summarize
from .library import library_items
from .logs import log
from .outcome import DECISION
from .reference import load_reference
from .storage import now, state_path, write_json
from .subtitles import is_hearing_impaired, parse_subtitle, timestamp

CORRECT_SECONDS = 0.5
MIN_TRIALS_FOR_AUTOMATIC = 30
CORRECT_SHARE = 0.9
SHIFT_RANGE_SECONDS = (2.0, 30.0)
AD_BREAK_SECONDS = 3.0
TRIAL_SHIFTED = "shifted"


def calibrate_audio(root, limit=None):
    random_shifts = numpy.random.default_rng(0)
    results = []
    for video, subtitle, _ in library_items(root):
        if limit and len(results) >= limit:
            break
        if not subtitle.lower().endswith(AUDIO_SYNC_FORMATS):
            continue
        truth = true_timings(video, subtitle)
        if truth is None:
            continue
        shift = float(random_shifts.choice([-1, 1]) * random_shifts.uniform(*SHIFT_RANGE_SECONDS))
        shift = abs(shift) if truth[0][0] + shift < 0 else shift
        results.append(trial(video, subtitle, truth, TRIAL_SHIFTED, [(start + shift, end + shift, text) for start, end, text in truth]))
        middle = truth[len(truth) // 2][0]
        results.append(trial(video, subtitle, truth, "ad break", [(start + shift + (AD_BREAK_SECONDS if start >= middle else 0),
                                                                    end + shift + (AD_BREAK_SECONDS if start >= middle else 0), text) for start, end, text in truth]))
        duration = truth[-1][1] + truth[0][0]
        results.append(trial(video, subtitle, truth, "unrelated", sorted((duration - end, duration - start, text) for start, end, text in truth)))
        print(*(summary_line(result) for result in results[-3:]), sep="\n", flush=True)
    report = {"time": now(), "root": root, "trials": results, **totals(results)}
    path = state_path("calibration", f"audio-{now().replace(':', '-')}.json")
    write_json(path, report)
    log("calibration.finished", f"audio calibration on {len(results)} trials, false positives={report['false_positives']}", trials=len(results),
        false_positives=report["false_positives"], report=path)
    print(json.dumps({key: value for key, value in report.items() if key != "trials"}, indent=1))
    return report


def true_timings(video, subtitle):
    try:
        cues = parse_subtitle(subtitle)["cues"]
        reference = load_reference(video, is_hearing_impaired(subtitle), len(cues)) if len(cues) >= MIN_CUES else None
    except (OSError, OnCueError):
        return None
    if reference is None:
        return None
    judged = judge(cues, reference)
    _, band = confidence(summarize(reference["kind"], *judged))
    if band != AUTOMATIC_BAND:
        return None
    decision, _, _, attempt = judged
    return cues if decision == DECISION["kept"] else attempt["timings"]


def trial(video, subtitle, truth, kind, cues):
    os.makedirs(state_path("calibration"), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=state_path("calibration")) as folder:
        copy = os.path.join(folder, "trial" + os.path.splitext(subtitle)[1])
        with open(copy, "w", encoding="utf-8") as handle:
            handle.write("\n".join(f"{number}\n{timestamp(max(start, 0))} --> {timestamp(max(end, 0))}\n{text}\n" for number, (start, end, text) in enumerate(cues, 1)))
        outcome, timings = audio_fix(video, copy, parse_subtitle(copy, confined=False))
    score, band = confidence(outcome)
    errors = numpy.abs(numpy.array([start for start, _, _ in timings]) - numpy.array([start for start, _, _ in truth])) if timings else None
    correct = kind == TRIAL_SHIFTED and errors is not None and float(numpy.mean(errors <= CORRECT_SECONDS)) >= CORRECT_SHARE
    return {"video": video, "subtitle": subtitle, "kind": kind, "confidence": score, "band": band, "correct": correct,
            "median_error": round(float(numpy.median(errors)), 2) if errors is not None else None, "agreement": outcome.get("agreement")}


def totals(results):
    confident = [result for result in results if result["band"] == AUTOMATIC_BAND]
    return {"count": len(results), "confident": len(confident), "confident_and_correct": sum(result["correct"] for result in confident),
            "false_positives": sum(not result["correct"] for result in confident),
            "missed": sum(result["correct"] and result["band"] != AUTOMATIC_BAND for result in results)}


def summary_line(result):
    return (f"{verdict(result):14s} {result['kind']:9s} confidence {result['confidence']:3d} {result['band']:6s} median error {result['median_error']}  "
            f"{os.path.basename(result['subtitle'])}")


def verdict(result):
    confident = result["band"] == AUTOMATIC_BAND
    if confident and not result["correct"]:
        return "FALSE POSITIVE"
    if result["correct"] and not confident:
        return "missed"
    if result["kind"] == TRIAL_SHIFTED and not result["correct"]:
        return "wrong, caught"
    return "ok"



def calibration_allows_automatic():
    folder = state_path("calibration")
    reports = sorted(name for name in os.listdir(folder) if name.startswith("audio-") and name.endswith(".json")) if os.path.isdir(folder) else []
    if not reports:
        return False, "Run oncue calibrate-audio on your library first.", {}
    with open(os.path.join(folder, reports[-1])) as handle:
        latest = json.load(handle)
    if latest["count"] < MIN_TRIALS_FOR_AUTOMATIC:
        return False, "The last calibration has {count} trials, it needs at least {needed}.", {"count": latest["count"], "needed": MIN_TRIALS_FOR_AUTOMATIC}
    if latest["false_positives"]:
        return False, "The last calibration found {count} confident audio syncs that were wrong.", {"count": latest["false_positives"]}
    return True, "The last calibration found no wrong confident result in {count} trials.", {"count": latest["count"]}
