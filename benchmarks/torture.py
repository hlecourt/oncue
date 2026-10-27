"""Misalign known-good subtitles in controlled ways, run the guard on each, and measure every line against its true time.

usage: torture.py WORK_FOLDER CORPUS_JSON [LABEL_FILTER]
CORPUS_JSON: [{"name": ..., "video": ..., "truth": synced subtitle, "other": synced subtitle of another episode or film}, ...]
"""
import json
import os
import sys
import time

import numpy

from subtitle_guard import alignment, reference, subtitles
from subtitle_guard.confidence import confidence
from subtitle_guard.guard import summarize

work, corpus = sys.argv[1], json.load(open(sys.argv[2]))
only = sys.argv[3] if len(sys.argv) > 3 else ""
os.makedirs(work, exist_ok=True)
PAL = 23.976 / 25
FILM = 23.976 / 24


def misaligned(cues, scale=1.0, offset=0.0, edits=(), fill_extra=False, rng=None, warp=lambda moment: moment):
    """Return (cues as the wrong source would have them, true start of each, None for invented lines)."""
    output = []
    for start, end, text in cues:
        shift, inside_missing = 0.0, False
        for at, duration, kind in edits:
            if kind == "video_extra" and at <= start < at + duration:
                inside_missing = True
            if kind == "video_extra" and start >= at + duration:
                shift -= duration
            if kind == "subtitle_extra" and start >= at:
                shift += duration
        if not inside_missing:
            output.append((warp(start + shift) * scale + offset, warp(end + shift) * scale + offset, text, start))
    for at, duration, kind in edits:
        if kind == "subtitle_extra" and fill_extra:
            shift_before = sum(-length for position, length, other in edits if other == "video_extra" and at >= position + length)
            shift_before += sum(length for position, length, other in edits if other == "subtitle_extra" and at > position)
            donors = [cue for cue in cues if cue[0] > at + 120][:int(duration / 3)]
            for number, (_, _, text) in enumerate(donors):
                moment = at + shift_before + 1 + number * 3
                output.append((moment * scale + offset, (moment + 2) * scale + offset, text, None))
    output.sort(key=lambda item: item[0])
    return [(start, end, text) for start, end, text, _ in output], [truth for _, _, _, truth in output]


def jitter(cues, rng, amount):
    return [(start + rng.uniform(-amount, amount), end + rng.uniform(-amount, amount), text) for start, end, text in cues]


def merged_pairs(cues):
    merged = []
    for index in range(0, len(cues) - 1, 2):
        merged.append((cues[index][0], cues[index + 1][1], cues[index][2] + "\n" + cues[index + 1][2]))
    return merged


def split_long(cues):
    output = []
    for start, end, text in cues:
        if end - start > 3:
            middle = (start + end) / 2
            output += [(start, middle, text), (middle + 0.04, end, text)]
            continue
        output.append((start, end, text))
    return output


def dense_windows(cues, count=3, width=90.0):
    starts = numpy.array([start for start, _, _ in cues])
    density = [(int(((starts >= moment) & (starts < moment + width)).sum()), moment) for moment in numpy.arange(0, starts[-1], 15.0)]
    chosen = []
    for _, moment in sorted(density, reverse=True):
        if all(abs(moment - other) >= width for other in chosen):
            chosen.append(float(moment))
        if len(chosen) == count:
            break
    return [(moment, moment + width) for moment in sorted(chosen)]


def inside(moment, windows):
    return any(first <= moment < last for first, last in windows)


def condensed(cues, windows, group=3):
    output, index = [], 0
    while index < len(cues):
        if inside(cues[index][0], windows) and index + group <= len(cues):
            members = cues[index:index + group]
            output.append((members[0][0], members[-1][1], "\n".join(text for _, _, text in members)))
            index += group
            continue
        output.append(cues[index])
        index += 1
    return output


def fragmented(cues, windows):
    output = []
    for start, end, text in cues:
        if not inside(start, windows):
            output.append((start, end, text))
            continue
        step = (end - start) / 3
        output += [(start + part * step, start + (part + 1) * step - 0.02, text) for part in range(3)]
    return output


def overlapping(cues, rng):
    output = list(cues)
    for start, end, text in cues:
        if rng.random() < 0.1:
            output.append((start + 0.2, end, "- " + text))
    return sorted(output)


def save(name, cues, subtitle_format):
    path = os.path.join(work, name + {"srt": ".srt", "utf16": ".srt", "vtt": ".vtt", "ass": ".ass"}[subtitle_format])
    if subtitle_format == "ass":
        header = ("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname\nStyle: Default,Arial\n\n[Events]\n"
                  "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        body = "".join(f"Dialogue: 0,{subtitles.ass_timestamp(start)},{subtitles.ass_timestamp(end)},Default,,0,0,0,,{text.replace(chr(10), chr(92) + 'N')}\n"
                       for start, end, text in cues)
        open(path, "w", encoding="utf-8").write(header + body)
        return path
    subtitles.create_subtitle(path, cues, "vtt" if subtitle_format == "vtt" else "srt")
    if subtitle_format == "utf16":
        content = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-16").write(content)
    return path


def run(video, path, hearing_impaired=False):
    document = subtitles.parse_subtitle(path)
    cues = document["cues"]
    if len(cues) < alignment.MIN_CUES:
        return "unverified", cues, None, "  0 none"
    embedded = reference.load_reference(video, hearing_impaired, len(cues))
    if embedded is None:
        return "unverified", cues, None, "  0 none"
    judged = alignment.judge(cues, embedded)
    decision, _, _, attempt = judged
    score, band = confidence(summarize(embedded["kind"], *judged))
    if decision == "kept":
        return decision, cues, attempt, f"{score:3d} {band}"
    return decision, attempt["timings"], attempt, f"{score:3d} {band}"


def cases(truth_cues, other_cues, rng):
    duration = truth_cues[-1][0]
    point = lambda share: duration * share
    yield "identity", "fix", misaligned(truth_cues), "srt"
    yield "offset +2.5s", "fix", misaligned(truth_cues, offset=2.5), "srt"
    yield "offset -45s", "fix", misaligned(truth_cues, offset=-45), "srt"
    yield "offset +250s", "fix", misaligned(truth_cues, offset=250), "srt"
    yield "offset +600s (wide search)", "fix", misaligned(truth_cues, offset=600), "srt"
    yield "offset +1500s (beyond the wide search)", "reject", misaligned(truth_cues, offset=1500), "srt"
    yield "fps 25 subtitle on 23.976 video", "fix", misaligned(truth_cues, scale=PAL), "srt"
    yield "fps 23.976 subtitle on 25 video", "fix", misaligned(truth_cues, scale=1 / PAL), "srt"
    yield "fps 24 vs 23.976 (0.1% drift)", "fix", misaligned(truth_cues, scale=FILM), "srt"
    yield "fps 25 vs 24", "fix", misaligned(truth_cues, scale=24 / 25), "srt"
    yield "fps 25 + offset 8s", "fix", misaligned(truth_cues, scale=PAL, offset=8), "srt"
    yield "video has an extra 35s scene", "fix", misaligned(truth_cues, edits=[(point(0.4), 35, "video_extra")]), "srt"
    yield "subtitle has an extra 35s gap", "fix", misaligned(truth_cues, edits=[(point(0.4), 35, "subtitle_extra")]), "srt"
    yield "subtitle has an extra scene with lines", "either", misaligned(truth_cues, edits=[(point(0.4), 35, "subtitle_extra")], fill_extra=True), "srt"
    yield "different cut, 5 edits (Netflix vs Blu-ray)", "fix", misaligned(truth_cues, edits=[(point(0.12), 6, "video_extra"), (point(0.3), 14, "subtitle_extra"),
                                                                                              (point(0.5), 3, "video_extra"), (point(0.68), 40, "video_extra"),
                                                                                              (point(0.85), 9, "subtitle_extra")]), "srt"
    yield "TV subtitle with 3 ad breaks of 120s", "fix", misaligned(truth_cues, edits=[(point(share), 120, "subtitle_extra") for share in (0.25, 0.5, 0.75)]), "srt"
    yield "TV subtitle with 4 ad breaks of 150s (600s total)", "fix", misaligned(truth_cues, edits=[(point(share), 150, "subtitle_extra") for share in (0.2, 0.4, 0.6, 0.8)]), "srt"
    yield "different cut + fps 25 + offset", "fix", misaligned(truth_cues, scale=PAL, offset=-12, edits=[(point(0.3), 20, "video_extra"), (point(0.7), 15, "subtitle_extra")]), "srt"
    yield "sloppy timing +-0.25s + offset 3s", "fix", misaligned(jitter(truth_cues, rng, 0.25), offset=3), "srt"
    yield "sloppy timing +-0.6s + offset 3s", "either", misaligned(jitter(truth_cues, rng, 0.6), offset=3), "srt"
    yield "lines merged in pairs + offset 4s", "fix", misaligned(merged_pairs(truth_cues), offset=4), "srt"
    yield "long lines split + offset -6s", "fix", misaligned(split_long(truth_cues), offset=-6), "srt"
    yield "40% of lines missing + offset 7s", "fix", misaligned([cue for cue in truth_cues if rng.random() > 0.4], offset=7), "srt"
    yield "only the first half + offset 5s", "fix", misaligned(truth_cues[:len(truth_cues) // 2], offset=5), "srt"
    yield "ASS file + offset 6s", "fix", misaligned(truth_cues, offset=6), "ass"
    yield "VTT file + fps 25", "fix", misaligned(truth_cues, scale=PAL), "vtt"
    yield "UTF-16 file + offset -3s", "fix", misaligned(truth_cues, offset=-3), "utf16"
    windows = dense_windows(truth_cues)
    yield "speed: 1% slower subtitle", "fix", misaligned(truth_cues, scale=1.01), "srt"
    yield "speed: 1.5% faster subtitle", "fix", misaligned(truth_cues, scale=0.985), "srt"
    yield "speed: 0.3% slower subtitle", "fix", misaligned(truth_cues, scale=1.003), "srt"
    yield "speed: 5% slower subtitle", "either", misaligned(truth_cues, scale=1.05), "srt"
    yield "speed: 20% slower subtitle", "either", misaligned(truth_cues, scale=1.2), "srt"
    yield "speed: 25/23.976 and 0.1% together", "fix", misaligned(truth_cues, scale=PAL * FILM), "srt"
    yield "speed: changes mid-file (x1.006 after 50%)", "fix", misaligned(truth_cues, warp=lambda moment: moment if moment < point(0.5) else point(0.5) + (moment - point(0.5)) * 1.006), "srt"
    yield "speed: 1% slower + cut + offset 9s", "fix", misaligned(truth_cues, scale=1.01, offset=9, edits=[(point(0.45), 25, "video_extra")]), "srt"
    yield "fast: dense stretches condensed 3 to 1 + offset 5s", "fix", misaligned(condensed(truth_cues, windows), offset=5), "srt"
    yield "fast: dense stretches split in fragments + offset -4s", "fix", misaligned(fragmented(truth_cues, windows), offset=-4), "srt"
    yield "fast: two speakers overlapping + offset 3s", "fix", misaligned(overlapping(truth_cues, rng), offset=3), "srt"
    yield "fast: every line 0.4s long + offset 2s", "fix", misaligned([(start, start + 0.4, text) for start, _, text in truth_cues], offset=2), "srt"
    yield "fast: only the dense stretches 3s late", "fix", misaligned(truth_cues, warp=lambda moment: moment + 3 if inside(moment, windows) else moment), "srt"
    yield "fast: dense stretches 3s late + fps 25 + offset", "fix", misaligned(truth_cues, scale=PAL, offset=-7, warp=lambda moment: moment + 3 if inside(moment, windows) else moment), "srt"
    if other_cues:
        yield "subtitle of another episode", "reject", ([cue for cue in other_cues], [None] * len(other_cues)), "srt"
    shuffled = sorted((rng.uniform(0, duration), text) for _, _, text in truth_cues)
    yield "random times", "reject", ([(start, start + 2, text) for start, text in shuffled], [None] * len(shuffled)), "srt"


totals = {}
for item in corpus:
    truth_cues = subtitles.parse_subtitle(item["truth"])["cues"]
    other_cues = subtitles.parse_subtitle(item["other"])["cues"] if item.get("other") else None
    rng = numpy.random.default_rng(7)
    for label, expectation, (cues, truths), subtitle_format in cases(truth_cues, other_cues, rng):
        if only not in label:
            continue
        started = time.time()
        path = save(f"{item['name']}-case", cues, subtitle_format)
        decision, timings, attempt, trust = run(item["video"], path)
        real = [(timing[0], truth) for timing, truth in zip(timings, truths) if truth is not None] if decision != "unverified" else []
        errors = numpy.abs(numpy.array([start - truth for start, truth in real])) if real else numpy.array([])
        within_fine = float(numpy.mean(errors <= 0.3)) if len(errors) else 0.0
        within_second = float(numpy.mean(errors <= 1.0)) if len(errors) else 0.0
        windows = dense_windows(truth_cues)
        dense_errors = numpy.array([abs(start - truth) for start, truth in real if inside(truth, windows)])
        dense_second = float(numpy.mean(dense_errors <= 1.0)) if len(dense_errors) else float("nan")
        accepted = decision in ("kept", "fixed")
        verdict = {"fix": accepted and within_second >= 0.9, "reject": not accepted, "either": True}[expectation]
        if expectation == "either":
            verdict_text = "info"
        else:
            verdict_text = "ok" if verdict else "FAIL"
        totals.setdefault(label, []).append(verdict_text)
        segments = f"{attempt['segments']:3d} seg short {attempt['short_segment_share']:4.0%} unsupported {attempt['unsupported_share']:4.0%}" if attempt else "   -              "
        print(f"{verdict_text:4s} {item['name']:14s} {label[:44]:44s} {decision:10s} confidence {trust:11s} lines<=0.3s {within_fine:4.0%} <=1s {within_second:4.0%} dense<=1s {dense_second:4.0%} "
              f"median {numpy.median(errors) if len(errors) else float('nan'):6.2f}s {segments} {time.time() - started:4.1f}s", flush=True)
print("\nsummary per case:")
for label, verdicts in totals.items():
    print(f"  {label[:52]:52s} ok {verdicts.count('ok'):2d}  fail {verdicts.count('FAIL'):2d}  info {verdicts.count('info'):2d}")
failures = sum(verdicts.count('FAIL') for verdicts in totals.values())
print(f"total failures: {failures}")
sys.exit(1 if failures else 0)
