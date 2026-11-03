# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure whether OnCue's timing is right by text, and whether its confidence can be trusted.

usage: text_truth.py DATA_FOLDER [--engine MODULE] [--only TEXT] [--jobs N] [--output RESULTS_JSON] [--library-only | --synthetic-only] [--split]

DATA_FOLDER holds:
  library.jsonl   one subtitle per line: {"subtitle", "raw" (base64), "original_raw" (base64, optional), "hearing_impaired",
                  "tracks": [{"index", "language", "image", "sdh", "starts", "ends"}, ...]}
  labels.json     {subtitle file name: {"intervals": {line number: [earliest, latest, true start]}, "base": true if a version is in sync,
                  "base_version": "current" or "original", "stray": [line numbers off in that version]}}; line numbers count from 1 in the original file. A line reads right when it starts within 0.5 s of
                  its true start (its time in a version read as in sync, else the start of the English line it translates), or inside its
                  interval: from 0.4 s before that English line starts to 0.4 s before the next one does, so it is the line on screen
  texts/          optional, <subtitle file name>.<track index>.srt: the text of a reference track, for engines that read it

  corrections.json  optional, dense-read findings as negative labels: {audit label: {"current" | "original": [[first line, last line, seconds to add]]}}
  track_texts.json  optional, {video: {track index: [[start, end, text], ...]}}: real text for synthetic cases built from embedded tracks

Rewritten library files are measured twice, as OnCue found them ([original]) and as they are now ([current]).
A synthetic line counts as right when its final start is within 0.5 s of its true start, a library line as described above. A result is
also wrong when an annotated bad span stays wrong (under 75 % of its lines right) or a line that read right before now reads wrong.
Results at 90 or more are the release gate (90 is the write threshold); the count at 80 is exploratory. --split also reports two halves of
the cases grouped by episode (every version and synthetic variant of an episode in the same half), to see how much the numbers move.
A synthetic result is right when 90 % of its lines are right and no 2-minute window of 6 lines or more falls below 75 %; a library result
(read samples) when 95 % of its read lines are right and no 2-minute window of 3 read lines or more has more than one wrong in three. Synthetic cases damage subtitles known to be in sync (embedded
tracks in another language, and library files labelled in sync) against the real reference track of the same video, so French cue splitting
and SDH density are those of real files.

The engine is a module with run(cues, tracks, hearing_impaired, texts[, speech]) -> {"decision", "confidence", "timings"}; the default runs
OnCue. An engine that takes `speech` gets the video's speech mask from ONCUE_SPEECH_FOLDER (<video name>.speech.npy, one boolean per 10 ms), or None.
"""
import argparse
import base64
import hashlib
import importlib
import inspect
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy

LINE_TOLERANCE_SECONDS = 0.5
WINDOW_SECONDS = 120
RIGHT_RULES = {"synthetic": (0.9, 6, 0.75), "library": (0.95, 3, 0.67)}
BASE_STRAY_MARGIN = 8
TRUSTED_CONFIDENCE = 90
EXPLORATORY_CONFIDENCE = 80
SPAN_REPAIRED_SHARE = 0.75
PAL = 23.976 / 25
SYNTHETIC_LANGUAGES = ("fre", "fra", "fr")
SHORT_REPLIES = ("Oui.", "Non.", "D'accord.", "Quoi ?")
OCR_SUBSTITUTIONS = {"l": "I", "I": "l", "o": "0", "e": "c", "m": "rn", "a": "o", "i": "!", "t": "f"}
ACCEPTED = ("kept", "fixed")


def main():
    arguments = parse_arguments()
    data = load_data(arguments.data)
    cases = []
    if not arguments.synthetic_only:
        cases += library_cases(data)
    if not arguments.library_only:
        cases += synthetic_cases(data)
    cases = [case for case in cases if arguments.only in case["name"]]
    started = time.time()
    with ProcessPoolExecutor(max_workers=arguments.jobs, initializer=load_engine, initargs=(arguments.engine,)) as pool:
        results = list(pool.map(run_case, cases, chunksize=1))
    for result in sorted(results, key=lambda item: item["name"]):
        print(describe(result))
    summary = summarize(results)
    if arguments.split:
        summary["halves"] = {half: summarize([result for result in results if episode_half(result["episode"]) == half]) for half in ("A", "B")}
    print(json.dumps(summary, indent=1))
    print(f"{len(results)} cases in {time.time() - started:.0f} s")
    if arguments.output:
        with open(arguments.output, "w") as handle:
            json.dump({"summary": summary, "results": [{key: value for key, value in result.items() if key != "timings"} for result in results]}, handle)


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("data")
    parser.add_argument("--engine", default="text_truth_oncue")
    parser.add_argument("--split", action="store_true")
    parser.add_argument("--only", default="")
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    parser.add_argument("--output")
    parser.add_argument("--library-only", action="store_true")
    parser.add_argument("--synthetic-only", action="store_true")
    return parser.parse_args()


def load_data(folder):
    with open(os.path.join(folder, "library.jsonl")) as handle:
        records = [json.loads(line) for line in handle]
    labels_file = os.path.join(folder, "labels.json")
    labels = {}
    if os.path.exists(labels_file):
        with open(labels_file) as handle:
            labels = json.load(handle)
    corrections, track_texts = {}, {}
    if os.path.exists(os.path.join(folder, "corrections.json")):
        with open(os.path.join(folder, "corrections.json")) as handle:
            by_label = {key: value for key, value in json.load(handle).items() if not key.startswith("_")}
        names = {label.get("label"): name for name, label in labels.items()}
        corrections = {names[key]: value for key, value in by_label.items() if key in names}
    if os.path.exists(os.path.join(folder, "track_texts.json")) and os.path.getsize(os.path.join(folder, "track_texts.json")):
        with open(os.path.join(folder, "track_texts.json")) as handle:
            track_texts = json.load(handle)
    return {"records": records, "labels": labels, "texts": os.path.join(folder, "texts"), "corrections": corrections, "track_texts": track_texts}


def parsed_cues(raw):
    from oncue.subtitles import parse_subtitle

    return parse_subtitle("", raw=base64.b64decode(raw))["cues"]


def reference_texts(data, name, tracks, hearing_impaired, cue_count):
    from oncue.reference import choose_reference
    from oncue.subtitles import parse_timed_text

    chosen = choose_reference(tracks, hearing_impaired, cue_count)
    path = os.path.join(data["texts"], f"{name}.{chosen['index']}.srt") if chosen else ""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as handle:
        return parse_timed_text(handle.read().replace("\r\n", "\n"))


def library_cases(data):
    # every version is its own case: a rewritten file is measured as OnCue found it (original) and as it is now (current)
    cases = []
    for record in data["records"]:
        name = os.path.basename(record["subtitle"])
        label = data["labels"].get(name)
        if label is None or not record.get("tracks"):
            continue
        versions = [("original", record["original_raw"]), ("current", record["raw"])] if record.get("original_raw") else [("current", record["raw"])]
        for version, raw in versions:
            cues = parsed_cues(raw)
            truths = label_truths(label, cues)
            spans = []
            for first, last, shift in data["corrections"].get(name, {}).get(version, []):
                for index in range(first - 1, min(last, len(cues))):
                    truths[index] = round(cues[index][0] + shift, 3)
                spans.append((first - 1, min(last, len(cues)) - 1))
            if sum(truth is not None for truth in truths) < 20:
                continue
            original = measure(cues, cues, truths, "library", spans)
            cases.append({"name": f"library {name} [{version}]", "kind": "library", "expect": "keep" if original["original_right"] else "fix",
                          "episode": record.get("video") or name,
                          "cues": cues, "truths": truths, "spans": spans, "tracks": record["tracks"],
                          "hearing_impaired": record.get("hearing_impaired", False), "video": record.get("video"),
                          "texts": reference_texts(data, name, record["tracks"], record.get("hearing_impaired", False), len(cues))})
    return cases


def label_truths(label, cues):
    truths = [None] * len(cues)
    for number, interval in label["intervals"].items():
        if 0 < int(number) <= len(cues):
            truths[int(number) - 1] = tuple(interval)
    return truths


def synthetic_bases(data):
    bases = []
    for record in data["records"]:
        name = os.path.basename(record["subtitle"])
        tracks = record.get("tracks") or []
        for track in tracks:
            if track["language"] in SYNTHETIC_LANGUAGES and not track["image"] and len(track["starts"]) >= 100:
                others = [other for other in tracks if other["index"] != track["index"]]
                cues = track_cues(data, record.get("video"), track)
                bases.append({"name": f"{name.split('.fr.')[0][:28]}#{track['index']}{'sdh' if track['sdh'] else ''}", "cues": cues, "tracks": others,
                              "hearing_impaired": track["sdh"], "video": record.get("video"),
                              "texts": reference_texts(data, name, others, track["sdh"], len(cues))})
        label = data["labels"].get(name)
        if label and label.get("base") and tracks:
            cues = parsed_cues(record["raw"] if label.get("base_version") == "current" else record.get("original_raw") or record["raw"])
            doubtful = {line - 1 + step for line in label.get("stray", []) for step in range(-BASE_STRAY_MARGIN, BASE_STRAY_MARGIN + 1)}
            in_sync = [cue for index, cue in enumerate(cues) if index not in doubtful]
            bases.append({"name": name.split(".fr.")[0][:30], "cues": in_sync, "tracks": tracks, "hearing_impaired": record.get("hearing_impaired", False),
                          "video": record.get("video"),
                          "texts": reference_texts(data, name, tracks, record.get("hearing_impaired", False), len(cues))})
    unique = {}
    for base in bases:
        unique.setdefault(base["name"], base)
    return list(unique.values())


def track_cues(data, video, track):
    # the embedded track's own text when it was extracted (track_texts.json), so text-reading engines see real dialogue
    lines = {round(start, 2): line_text for start, _, line_text in data["track_texts"].get(video or "", {}).get(str(track["index"]), [])}
    return [(start, end, lines.get(round(start, 2), "x")) for start, end in zip(track["starts"], track["ends"], strict=False)]


def synthetic_cases(data):
    bases = synthetic_bases(data)
    cases = []
    for number, base in enumerate(bases):
        other = next(bases[(number + step) % len(bases)] for step in range(1, len(bases)) if bases[(number + step) % len(bases)]["video"] != base["video"])
        rng = numpy.random.default_rng(7)
        for label, expect, (cues, truths) in damages(base["cues"], other["cues"], rng):
            cases.append({"name": f"synthetic {base['name']} / {label}", "kind": "synthetic", "expect": expect, "cues": cues, "truths": truths,
                          "tracks": base["tracks"], "hearing_impaired": base["hearing_impaired"], "texts": base["texts"], "damage": label,
                          "video": base["video"], "episode": base["video"] or base["name"]})
        if base["texts"]:
            # a picture reference read by OCR: the same short passage damage, against a reference text with character errors and missing lines
            cues, truths = passages_shifted(base["cues"], ((len(base["cues"]) // 2, 12),), 6.0)
            label = "OCR-noisy reference + passage of 12 lines 6s late"
            cases.append({"name": f"synthetic {base['name']} / {label}", "kind": "synthetic", "expect": "fix", "cues": cues, "truths": truths,
                          "tracks": base["tracks"], "hearing_impaired": base["hearing_impaired"], "texts": ocr_noise(base["texts"], rng), "damage": label,
                          "video": base["video"], "episode": base["video"] or base["name"]})
    return cases


def ocr_noise(texts, rng):
    noisy = []
    for start, end, text in texts:
        if rng.random() < 0.05:
            continue
        noisy.append((start, end, "".join(OCR_SUBSTITUTIONS.get(letter, letter) if rng.random() < 0.08 else letter for letter in text)))
    return noisy


def episode_half(episode):
    return "A" if int(hashlib.sha1((episode or "").encode()).hexdigest(), 16) % 2 == 0 else "B"


def damages(cues, other_cues, rng):
    duration = cues[-1][0]

    def point(share):
        return duration * share

    yield "identity", "keep", misaligned(cues)
    yield "offset +2.5s", "fix", misaligned(cues, offset=2.5)
    yield "offset -45s", "fix", misaligned(cues, offset=-45)
    yield "offset +250s", "fix", misaligned(cues, offset=250)
    yield "fps 25 on 23.976", "fix", misaligned(cues, scale=PAL)
    yield "fps 23.976 on 25 + offset 8s", "fix", misaligned(cues, scale=1 / PAL, offset=8)
    yield "video has an extra 35s scene", "fix", misaligned(cues, edits=[(point(0.4), 35, "video_extra")])
    yield "subtitle has an extra 35s gap", "fix", misaligned(cues, edits=[(point(0.4), 35, "subtitle_extra")])
    yield "different cut, 5 edits", "fix", misaligned(cues, edits=[(point(0.12), 6, "video_extra"), (point(0.3), 14, "subtitle_extra"),
                                                                    (point(0.5), 3, "video_extra"), (point(0.68), 40, "video_extra"),
                                                                    (point(0.85), 9, "subtitle_extra")])
    yield "3 ad breaks of 120s", "fix", misaligned(cues, edits=[(point(share), 120, "subtitle_extra") for share in (0.25, 0.5, 0.75)])
    yield "cut + fps 25 + offset", "fix", misaligned(cues, scale=PAL, offset=-12, edits=[(point(0.3), 20, "video_extra"), (point(0.7), 15, "subtitle_extra")])
    yield "one passage of 40 lines 14s early", "fix", passage_shifted(cues, len(cues) // 3, 40, -14.0)
    yield "two passages of 12 lines 5s late", "fix", passages_shifted(cues, ((len(cues) // 4, 12), (len(cues) * 2 // 3, 12)), 5.0)
    yield "one passage of 20 lines 3s early", "fix", passages_shifted(cues, ((len(cues) // 2, 20),), -3.0)
    yield "two passages of 12 lines one line late", "fix", one_line_slides(cues, ((len(cues) // 4, 12), (len(cues) * 2 // 3, 12)))
    for count, shift in ((2, 6.0), (3, -8.0), (4, 5.0), (8, -10.0), (12, 12.0)):
        yield f"{count} lines misplaced by {shift:+.0f}s", "fix", passages_shifted(cues, ((len(cues) // 3, count),), shift)
    yield "8 short repeated replies 6s late", "fix", replies_shifted(cues, len(cues) * 3 // 5, 8, 6.0)
    yield "long lines split + offset -6s", "fix", shifted(*split_long(cues), -6)
    yield "lines merged in pairs + offset 4s", "fix", misaligned(merged_pairs(cues), offset=4)
    yield "sloppy +-0.25s + offset 3s", "fix", misaligned(jitter(cues, rng, 0.25), offset=3)
    yield "subtitle of another video", "reject", ([(start, end, text) for start, end, text in other_cues], [None] * len(other_cues))
    shuffled = sorted(rng.uniform(0, duration) for _ in cues)
    yield "random times", "reject", ([(start, start + 2, cue[2]) for start, cue in zip(shuffled, cues, strict=False)], [None] * len(shuffled))


def misaligned(cues, scale=1.0, offset=0.0, edits=()):
    """Return (cues as the wrong source would have them, true start of each)."""
    output = []
    for start, end, text in cues:
        shift, missing = 0.0, False
        for at, length, kind in edits:
            if kind == "video_extra" and at <= start < at + length:
                missing = True
            if kind == "video_extra" and start >= at + length:
                shift -= length
            if kind == "subtitle_extra" and start >= at:
                shift += length
        if not missing:
            output.append(((start + shift) * scale + offset, (end + shift) * scale + offset, text, start))
    output.sort(key=lambda item: item[0])
    return [(start, end, text) for start, end, text, _ in output], [truth for _, _, _, truth in output]


def passage_shifted(cues, first, count, shift):
    return passages_shifted(cues, ((first, count),), shift)


def passages_shifted(cues, passages, shift):
    """Shift passages that sit between gaps wide enough to take the shift without overlapping their neighbours, as a cut missing a shot does."""
    inside = set()
    for first, count in passages:
        start = between_gaps(cues, first, count, abs(shift) + 0.5)
        inside |= set(range(start, start + count))
    moved = [(start + shift, end + shift, text) if index in inside else (start, end, text) for index, (start, end, text) in enumerate(cues)]
    order = sorted(range(len(cues)), key=lambda index: moved[index][0])
    return [moved[index] for index in order], [cues[index][0] for index in order]


def replies_shifted(cues, first, count, shift):
    # a stretch of one-word replies ("Oui.", "Non.") that text can hardly tell apart, moved as a passage
    start = between_gaps(cues, first, count, abs(shift) + 0.5)
    replaced = [(begin, end, SHORT_REPLIES[index % len(SHORT_REPLIES)]) if start <= index < start + count else (begin, end, text)
                for index, (begin, end, text) in enumerate(cues)]
    return passages_shifted(replaced, ((start, count),), shift)


def between_gaps(cues, first, count, gap):
    for start in list(range(first, len(cues) - count - 1)) + list(range(1, first)):
        if cues[start][0] - cues[start - 1][1] >= gap and cues[start + count][0] - cues[start + count - 1][1] >= gap:
            return start
    return first


def one_line_slides(cues, passages):
    moved = list(cues)
    for first, count in passages:
        for index in range(first, min(first + count, len(cues) - 1)):
            shift = cues[index + 1][0] - cues[index][0]
            moved[index] = (cues[index][0] + shift, cues[index][1] + shift, cues[index][2])
    order = sorted(range(len(cues)), key=lambda index: moved[index][0])
    return [moved[index] for index in order], [cues[index][0] for index in order]


def split_long(cues):
    output, truths = [], []
    for start, end, text in cues:
        if end - start > 3:
            middle = (start + end) / 2
            words = text.split()
            output += [(start, middle, " ".join(words[:len(words) // 2]) or text), (middle + 0.04, end, " ".join(words[len(words) // 2:]) or text)]
            truths += [start, middle + 0.04]
            continue
        output.append((start, end, text))
        truths.append(start)
    return output, truths


def merged_pairs(cues):
    return [(cues[index][0], cues[index + 1][1], f"{cues[index][2]} {cues[index + 1][2]}") for index in range(0, len(cues) - 1, 2)]


def jitter(cues, rng, amount):
    return [(start + rng.uniform(-amount, amount), end + rng.uniform(-amount, amount), text) for start, end, text in cues]


def shifted(cues, truths, offset):
    return [(start + offset, end + offset, text) for start, end, text in cues], truths


ENGINE = {}


def load_engine(name):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    ENGINE["module"] = importlib.import_module(name)


def run_case(case):
    cues, truths = case["cues"], case["truths"]
    started = time.time()
    extra = {"speech": speech_mask(case.get("video"))} if "speech" in inspect.signature(ENGINE["module"].run).parameters else {}
    outcome = ENGINE["module"].run(cues, case["tracks"], case["hearing_impaired"], case["texts"], **extra)
    timings = outcome["timings"] or cues
    return {**{key: case[key] for key in ("name", "kind", "expect")}, "episode": case.get("episode"),
            **measure(cues, timings, truths, case["kind"], case.get("spans", ())),
            "decision": outcome["decision"],
            "confidence": outcome["confidence"], "seconds": round(time.time() - started, 1), "timings": timings, "detail": outcome.get("detail")}


def speech_mask(video):
    # optional: ONCUE_SPEECH_FOLDER holds <video name>.speech.npy, one boolean per 10 ms of detected speech
    folder = os.environ.get("ONCUE_SPEECH_FOLDER")
    if not folder or not video:
        return None
    path = os.path.join(folder, os.path.splitext(os.path.basename(video))[0] + ".speech.npy")
    return numpy.load(path) if os.path.exists(path) else None


def measure(cues, timings, truths, kind, spans=()):
    """Right means the share and window rule, plus every annotated bad span repaired and no line that read right before now wrong."""
    known = [(index, timing[0], truth, cue[0]) for index, (timing, truth, cue) in enumerate(zip(timings, truths, cues, strict=False)) if truth is not None]
    if not known:
        return {"known": 0, "right_share": None, "worst_window": None, "right": None, "original_right": None, "moved_share": moved_share(cues, timings),
                "spans": 0, "spans_repaired": 0, "damaged_lines": 0}
    final_right = numpy.array([reads_right(start, truth) for _, start, truth, _ in known])
    original_right = numpy.array([reads_right(original, truth) for _, _, truth, original in known])
    moments = numpy.array([truth[0] if isinstance(truth, tuple) else truth for _, _, truth, _ in known])
    by_line = {index: (now, before) for (index, _, _, _), now, before in zip(known, final_right, original_right, strict=False)}
    repaired = [span_repaired(by_line, span, 0) for span in spans]
    already = [span_repaired(by_line, span, 1) for span in spans]
    damaged = int(numpy.sum(original_right & ~final_right))
    return {"known": len(known), "right_share": round(float(final_right.mean()), 3), "worst_window": worst_window(moments, final_right, kind),
            "right": bool(is_right(moments, final_right, kind) and all(repaired) and damaged == 0), "original_share": round(float(original_right.mean()), 3),
            "original_right": bool(is_right(moments, original_right, kind) and all(already)), "moved_share": moved_share(cues, timings),
            "spans": len(spans), "spans_repaired": int(sum(repaired)), "damaged_lines": damaged}


def span_repaired(by_line, span, column):
    lines = [by_line[index][column] for index in range(span[0], span[1] + 1) if index in by_line]
    return bool(lines) and numpy.mean(lines) >= SPAN_REPAIRED_SHARE


def reads_right(start, truth):
    if isinstance(truth, tuple):
        return truth[0] <= start < truth[1] or abs(start - truth[2]) <= LINE_TOLERANCE_SECONDS
    return abs(start - truth) <= LINE_TOLERANCE_SECONDS


def worst_window(moments, right, kind):
    minimum = RIGHT_RULES[kind][1]
    shares = [float(right[(moments >= first) & (moments < first + WINDOW_SECONDS)].mean())
              for first in numpy.arange(0, moments.max() + 1, WINDOW_SECONDS / 2)
              if ((moments >= first) & (moments < first + WINDOW_SECONDS)).sum() >= minimum]
    return round(min(shares), 2) if shares else None


def is_right(moments, right, kind):
    window = worst_window(moments, right, kind)
    share, _, window_share = RIGHT_RULES[kind]
    return bool(right.mean() >= share and (window is None or window >= window_share))


def moved_share(cues, timings):
    return round(float(numpy.mean([abs(timing[0] - cue[0]) > LINE_TOLERANCE_SECONDS for timing, cue in zip(timings, cues, strict=False)])), 3)


def summarize(results):
    summary = {}
    for kind in ("library", "synthetic", "all"):
        chosen = [result for result in results if kind in ("all", result["kind"])]
        if chosen:
            summary[kind] = summary_of(chosen)
    return summary


def summary_of(results):
    measured = [result for result in results if result["expect"] != "either"]
    trusted = [result for result in measured if result["decision"] in ACCEPTED and result["confidence"] >= TRUSTED_CONFIDENCE]
    exploratory = [result for result in measured if result["decision"] in ACCEPTED and result["confidence"] >= EXPLORATORY_CONFIDENCE]
    in_sync = [result for result in results if result["original_right"]]
    fixable = [result for result in results if result["expect"] == "fix" and result["original_right"] is False]
    rejectable = [result for result in results if result["expect"] == "reject"]
    adversarial = [result for result in results if result["expect"] == "either"]
    return {
        "cases": len(results),
        "trusted_90": {"count": len(trusted), "wrong": sum(not result["right"] for result in trusted if result["right"] is not None)
                       + sum(result["expect"] == "reject" for result in trusted),
                       "wrong_writes": sum(result["decision"] == "fixed" and result["right"] is False for result in trusted)},
        "exploratory_80": {"count": len(exploratory), "wrong": sum(not result["right"] for result in exploratory if result["right"] is not None)
                           + sum(result["expect"] == "reject" for result in exploratory),
                           "wrong_writes": sum(result["decision"] == "fixed" and result["right"] is False for result in exploratory)},
        "passages": {"spans": sum(result.get("spans", 0) for result in results), "repaired": sum(result.get("spans_repaired", 0) for result in results),
                     "false_verified_keeps": sum(result["decision"] == "kept" and result["confidence"] >= TRUSTED_CONFIDENCE
                                                 and result.get("spans_repaired", 0) < result.get("spans", 0) for result in results),
                     "damaged_lines": sum(result.get("damaged_lines", 0) for result in results)},
        "in_sync": {"count": len(in_sync), "untouched": sum(result["moved_share"] <= 0.01 for result in in_sync),
                    "kept_90": sum(result["decision"] == "kept" and result["confidence"] >= TRUSTED_CONFIDENCE for result in in_sync),
                    "damaged": sum(result["right"] is False for result in in_sync)},
        "fixable": {"count": len(fixable), "right": sum(bool(result["right"]) for result in fixable),
                    "right_90": sum(bool(result["right"]) and result["confidence"] >= TRUSTED_CONFIDENCE and result["decision"] in ACCEPTED for result in fixable)},
        "rejectable": {"count": len(rejectable), "accepted_90": sum(result["decision"] in ACCEPTED and result["confidence"] >= TRUSTED_CONFIDENCE
                                                                    for result in rejectable)},
        "adversarial": {"count": len(adversarial), "wrong_90": sum(result["right"] is False and result["decision"] in ACCEPTED and result["confidence"] >= TRUSTED_CONFIDENCE
                                                              for result in adversarial)},
        "line_share_right": round(float(numpy.mean([result["right_share"] for result in results if result["right_share"] is not None])), 3),
    }


def describe(result):
    verdict = {True: "right", False: "WRONG", None: "-"}[result["right"]]
    trusted = result["decision"] in ACCEPTED and result["confidence"] >= TRUSTED_CONFIDENCE
    alarm = "!!" if trusted and (result["right"] is False or result["expect"] == "reject") else "  "
    alarm = "(!)" if alarm == "!!" and result["expect"] == "either" else alarm
    share = f"{result['right_share']:.0%}" if result["right_share"] is not None else "-"
    original = f"{result.get('original_share', 0):.0%}" if result["right_share"] is not None else "-"
    return (f"{alarm} {result['name'][:78]:78s} {result['expect']:6s} {result['decision']:9s} {result['confidence']:3d} {verdict:5s} "
            f"lines {share:>4s} (was {original:>4s}) worst {result['worst_window']} moved {result['moved_share']:.0%} {result['seconds']:4.1f}s")


if __name__ == "__main__":
    main()
