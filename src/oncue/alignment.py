# SPDX-License-Identifier: GPL-3.0-or-later
"""Score a subtitle against a reference, realign it piecewise and decide whether the result can be trusted."""

import itertools
import math
from concurrent.futures import ThreadPoolExecutor

import numpy

from .outcome import DECISION

TOLERANCE_SECONDS = 0.3
WINDOW_SECONDS = 120
MIN_WINDOW_CUES = 8
MIN_CUES = 50
PASS_MARGIN = 0.25
WINDOW_PASS_MARGIN = 0.10
OBJECTIVE_MARGIN = 0.15
MIN_IMPROVEMENT = 0.01
NOISE_OFFSETS = (-97.0, -53.0, 37.0, 71.0, 113.0)
SCALES = (1.0, 25 / 23.976, 23.976 / 25)
SCALE_CHUNK_CUES = 30
DRIFT_CHUNK_CUES = 10
DRIFT_CHUNK_SUPPORT = 0.4
DRIFT_MIN_CHUNKS = 4
DRIFT_MIN_SLOPE = 0.002
DRIFT_MAX_SLOPE = 0.03
DRIFT_MIN_GAIN = 1.03
GRID_STEP_SECONDS = 0.05
GRID_TOLERANCE_SECONDS = TOLERANCE_SECONDS + GRID_STEP_SECONDS / 2
MAX_OFFSET_SECONDS = 400.0
WIDE_MAX_OFFSET_SECONDS = 1200.0
WIDE_GRID_STEP_SECONDS = 0.1
KEEP_SWITCH_PENALTY = 12.0
SEARCH_SWITCH_PENALTY = 5.0
REPAIR_SWITCH_PENALTY = 3.0
REPAIR_WINDOW_SECONDS = 120.0
REPAIR_FALSE_ALARM = 1e-6
REPAIR_MIN_LEAD = 3
REPAIR_LEAD_SHARE = 0.3
WEAK_RUN_LENGTHS = (8, 12, 20)
WEAK_RUN_PROBABILITY = 1e-3
STRENGTH_FULL_LEAD = 0.4
STRENGTH_FULL_LINES = 20
ORDER_SLACK_SECONDS = 0.5
CENTRING_NEIGHBOURS = 10
SHORT_SEGMENT_CUES = 15
SUPPORTED_SEGMENT_MATCHED = 0.55
MAX_UNSUPPORTED_SHARE = 0.05
END_WEIGHT = 0.5
BEFORE_VIDEO_PENALTY = 10.0
NOTICEABLE_SHIFT_SECONDS = 0.5
SAME_PART_SECONDS = 0.6
MIN_PART_LINES = 3
MAX_CUE_SECONDS = 30
MAX_CHECKED_CUES = 8000
MAX_TIMESTAMP_SECONDS = 48 * 3600
MAX_WRONG_END_SHARE = 0.05


def oversized_reason(cues):
    if len(cues) > MAX_CHECKED_CUES:
        return f"too many lines to check safely ({len(cues)}, at most {MAX_CHECKED_CUES})"
    if cues and max(max(start, end) for start, end, _ in cues) > MAX_TIMESTAMP_SECONDS:
        return f"times beyond {MAX_TIMESTAMP_SECONDS // 3600} hours, the file is not a real subtitle"
    return None


def wrong_end_share(cues):
    if not cues:
        return 0.0
    return sum(end < start or end - start > MAX_CUE_SECONDS for start, end, _ in cues) / len(cues)


def judge(cues, reference):
    original = score(cues, reference)
    attempt = assess(cues, reference, keep=original["passed"])
    if original["passed"] and not improves(cues, original, attempt):
        return DECISION["kept"], original, original["weak_minutes"], attempt
    return (DECISION["fixed"] if attempt["fixed"] else DECISION["rejected"]), original, original["weak_minutes"], attempt


def improves(cues, original, attempt):
    moved = any(abs(new_start - start) > NOTICEABLE_SHIFT_SECONDS for (new_start, _, _), (start, _, _) in zip(attempt["timings"], cues, strict=False))
    return attempt["fixed"] and moved and attempt["score"]["matched"] >= original["matched"] + MIN_IMPROVEMENT


def describe_changes(cues, attempt):
    scale = attempt.get("scale", 1.0)
    offsets = [new_start - start * scale for (new_start, _, _), (start, _, _) in zip(attempt["timings"], cues, strict=False)]
    return {"scale": float(scale), "parts": out_of_sync_parts(cues, offsets), "lines": len(cues)}


def out_of_sync_parts(cues, shifts):
    parts, current = [], []
    for index in sorted(range(len(cues)), key=lambda position: cues[position][0]):
        if abs(shifts[index]) < NOTICEABLE_SHIFT_SECONDS:
            parts, current = close_part(parts, current, cues, shifts), []
            continue
        if current and abs(shifts[index] - numpy.median([shifts[member] for member in current])) > SAME_PART_SECONDS:
            parts, current = close_part(parts, current, cues, shifts), []
        current.append(index)
    return close_part(parts, current, cues, shifts)


def close_part(parts, members, cues, shifts):
    if len(members) < MIN_PART_LINES:
        return parts
    return [*parts, {"first": cues[members[0]][0], "last": cues[members[-1]][0], "lines": len(members), "shift": round(float(numpy.median([shifts[member] for member in members])), 1)}]


def assess(cues, reference, wide_retry=True, keep=False):
    attempt = assess_on_grid(cues, reference, offset_grid(), keep)
    if attempt["fixed"] or keep or not wide_retry:
        return attempt
    return {**assess_on_grid(cues, reference, offset_grid(WIDE_MAX_OFFSET_SECONDS, WIDE_GRID_STEP_SECONDS), keep), "wide_search": True}


def assess_on_grid(cues, reference, grid, keep):
    mirrored = mirror(reference)
    with ThreadPoolExecutor(max_workers=1) as pool:
        null_alignment = pool.submit(align, cues, mirrored, grid, keep)
        aligned = align(cues, reference, grid, keep)
        null = null_alignment.result()
    aligned_score = score(aligned["timings"], reference)
    null_score = score(null["timings"], mirrored)
    fixed = (aligned_score is not None and aligned_score["passed"] and aligned["objective"] >= null["objective"] + OBJECTIVE_MARGIN
             and aligned["unsupported_share"] <= MAX_UNSUPPORTED_SHARE)
    return {"timings": aligned["timings"], "score": aligned_score, "null": null_score, "fixed": bool(fixed), "scale": aligned["scale"],
            "objective": aligned["objective"], "null_objective": null["objective"], "segments": aligned["segments"],
            "short_segment_share": aligned["short_segment_share"], "unsupported_share": aligned["unsupported_share"], "strength": aligned["strength"],
            "weak_minutes": aligned_score["weak_minutes"] if aligned_score else []}


def mirror(reference):
    end_of_video = max(reference["starts"].max(), reference["ends"].max() if len(reference["ends"]) else 0) + 1
    if len(reference["ends"]) < MIN_CUES:
        return {"kind": reference["kind"], "starts": numpy.sort(end_of_video - reference["starts"]), "ends": numpy.array([])}
    return {"kind": reference["kind"], "starts": numpy.sort(end_of_video - reference["ends"]), "ends": numpy.sort(end_of_video - reference["starts"])}


def score(cues, reference):
    if len(cues) < MIN_CUES:
        return None
    timed = sorted((start, end) for start, end, _ in cues)
    starts, ends = numpy.array([start for start, _ in timed]), numpy.array([end for _, end in timed])
    hits = coincidences(starts, ends, reference)
    matched = float(hits.mean())
    noise = chance(starts, ends, reference)
    windows = [(float(hits[inside].mean()), chance(starts[inside], ends[inside], reference))
               for first in numpy.arange(0, starts[-1] + 1, WINDOW_SECONDS / 2)
               for inside in [(starts >= first) & (starts < first + WINDOW_SECONDS)] if inside.sum() >= MIN_WINDOW_CUES]
    worst_window = min((local for local, _ in windows), default=matched)
    worst_margin = min((local - local_noise for local, local_noise in windows), default=matched - noise)
    weak_run = weakest_run(hits, matched, starts)
    passed = matched >= noise + PASS_MARGIN and worst_margin >= WINDOW_PASS_MARGIN
    return {"matched": round(matched, 3), "noise": round(noise, 3), "worst_window": round(worst_window, 3), "worst_margin": round(worst_margin, 3),
            "weak_run": round(weak_run["log_probability"], 1), "passed": bool(passed),
            "weak_minutes": weak_run["minutes"] if weak_run["log_probability"] < math.log10(WEAK_RUN_PROBABILITY) else []}


def chance(starts, ends, reference):
    return float(numpy.mean([coincidences(starts + offset, ends + offset, reference).mean() for offset in NOISE_OFFSETS]))


def coincidences(starts, ends, reference, tolerance=TOLERANCE_SECONDS):
    """A line coincides when its start meets a reference start or its end meets a reference end, so a line split in two still counts."""
    hits = numpy.abs(nearest_gap(starts, reference["starts"])) <= tolerance
    if len(reference["ends"]) < MIN_CUES:
        return hits
    return hits | (numpy.abs(nearest_gap(ends, reference["ends"])) <= tolerance)


def weakest_run(hits, matched, starts):
    weakest = {"log_probability": 0.0, "minutes": []}
    for length in WEAK_RUN_LENGTHS:
        if len(hits) < length:
            continue
        totals = numpy.convolve(hits.astype(int), numpy.ones(length, dtype=int), "valid")
        first = int(numpy.argmin(totals))
        log_probability = math.log10(max(binomial_at_most(int(totals[first]), length, matched) * len(hits) / length, 1e-300))
        if log_probability < weakest["log_probability"]:
            weakest = {"log_probability": log_probability, "minutes": list(range(int(starts[first] // 60), int(starts[first + length - 1] // 60) + 1))}
    return weakest


def binomial_at_most(found, count, chance_of_hit):
    return sum(binomial_probability(hits, count, chance_of_hit) for hits in range(0, found + 1))


def binomial_at_least(found, count, chance_of_hit):
    return sum(binomial_probability(hits, count, chance_of_hit) for hits in range(found, count + 1))


def binomial_probability(hits, count, chance_of_hit):
    chance_of_hit = min(max(chance_of_hit, 1e-9), 1 - 1e-9)
    return math.exp(math.lgamma(count + 1) - math.lgamma(hits + 1) - math.lgamma(count - hits + 1) + hits * math.log(chance_of_hit)
                    + (count - hits) * math.log(1 - chance_of_hit))


def nearest_gap(moved, reference):
    positions = numpy.clip(numpy.searchsorted(reference, moved), 1, len(reference) - 1)
    after, before = reference[positions] - moved, reference[positions - 1] - moved
    return numpy.where(numpy.abs(after) < numpy.abs(before), after, before)


def align(cues, reference, grid=None, keep=False):
    """Keep mode protects a timing that already passes: only an offset change with strong evidence survives. Otherwise follow the cuts."""
    grid = offset_grid() if grid is None else grid
    order = numpy.argsort([start for start, _, _ in cues], kind="stable")
    starts = numpy.array([cues[index][0] for index in order])
    ends = numpy.array([cues[index][1] for index in order])
    scale = choose_scale(starts, reference["starts"], grid)
    penalty = KEEP_SWITCH_PENALTY if keep else SEARCH_SWITCH_PENALTY
    offsets, objective = best_offsets(starts * scale, ends * scale, reference, grid, penalty, keep)
    offsets = repaired(starts * scale, ends * scale, offsets, reference, grid)
    segments = numpy.split(numpy.arange(len(offsets)), numpy.flatnonzero(numpy.diff(offsets)) + 1)
    lengths = numpy.array([len(segment) for segment in segments])
    strength = segment_strength(starts * scale, ends * scale, starts, offsets, reference, segments)
    offsets = center_segments(starts * scale, reference["starts"], offsets)
    timings = [None] * len(cues)
    for rank, index in enumerate(order):
        timings[index] = (starts[rank] * scale + offsets[rank], ends[rank] * scale + offsets[rank], cues[index][2])
    hits = coincidences(starts * scale + offsets, ends * scale + offsets, reference)
    unsupported = sum(len(segment) for segment in segments if hits[segment].mean() < SUPPORTED_SEGMENT_MATCHED)
    return {"timings": timings, "objective": objective / len(cues), "segments": len(segments), "scale": float(scale),
            "short_segment_share": float(lengths[lengths < SHORT_SEGMENT_CUES].sum() / len(cues)),
            "unsupported_share": float(unsupported / len(cues)), "strength": strength}


def repaired(starts, ends, offsets, reference, grid):
    """Move a passage to the offset a looser alignment finds, when it is weak where it is and too strong where it goes to be chance."""
    candidate, _ = best_offsets(starts, ends, reference, grid, REPAIR_SWITCH_PENALTY, either=False)
    current = center_segments(starts, reference["starts"], offsets)
    proposed = center_segments(starts, reference["starts"], candidate)
    current_hits = coincidences(starts + current, ends + current, reference)
    proposed_hits = coincidences(starts + proposed, ends + proposed, reference)
    noise = chance(starts + current, ends + current, reference)
    rate = float(current_hits.mean())
    trials = REPAIR_WINDOW_SECONDS / TOLERANCE_SECONDS
    result = offsets.copy()
    differs = numpy.abs(candidate - offsets) > NOTICEABLE_SHIFT_SECONDS
    boundaries = numpy.flatnonzero((numpy.diff(candidate) != 0) | (differs[1:] != differs[:-1])) + 1
    for members in numpy.split(numpy.arange(len(offsets)), boundaries):
        if not differs[members[0]] or numpy.abs(candidate[members] - offsets[members]).max() > REPAIR_WINDOW_SECONDS:
            continue
        found, before = int(proposed_hits[members].sum()), int(current_hits[members].sum())
        if found - before < max(REPAIR_MIN_LEAD, REPAIR_LEAD_SHARE * len(members)):
            continue
        if binomial_at_least(found, len(members), noise) * trials * binomial_at_most(before, len(members), rate) > REPAIR_FALSE_ALARM:
            continue
        result[members] = candidate[members]
    return result


def segment_strength(starts, ends, original_starts, offsets, reference, segments):
    """How clearly each moved segment beats its original timing and its neighbours' offsets; 1 when every one wins by far."""
    weakest = 1.0
    for number, members in enumerate(segments):
        own = starts[members] + offsets[members]
        if numpy.allclose(own, original_starts[members], atol=NOTICEABLE_SHIFT_SECONDS):
            continue
        own_rate = coincidences(own, ends[members] + offsets[members], reference, GRID_TOLERANCE_SECONDS).mean()
        alternatives = [original_starts[members] - starts[members]]
        alternatives += [numpy.full(len(members), offsets[segments[neighbour][0]]) for neighbour in (number - 1, number + 1) if 0 <= neighbour < len(segments)]
        for shift in alternatives:
            other_rate = coincidences(starts[members] + shift, ends[members] + shift, reference, GRID_TOLERANCE_SECONDS).mean()
            lead = (own_rate - other_rate) * min(1.0, len(members) / STRENGTH_FULL_LINES)
            weakest = min(weakest, max(0.0, min(1.0, lead / STRENGTH_FULL_LEAD)))
    return float(weakest)


def choose_scale(starts, reference, grid):
    def peaks(scale, chunk_cues):
        found = []
        for chunk_start in range(0, len(starts), chunk_cues):
            chunk = starts[chunk_start:chunk_start + chunk_cues]
            hits = sum(offset_hits(start * scale, reference, grid).astype(numpy.int16) for start in chunk)
            found.append((float(numpy.median(chunk)) * scale, float(grid[int(hits.argmax())]), int(hits.max()), len(chunk)))
        return found

    def support(scale):
        return sum(peak[2] for peak in peaks(scale, SCALE_CHUNK_CUES))

    supports = {scale: support(scale) for scale in SCALES}
    best = max(SCALES, key=supports.get)
    drifted = drift_scale(best, peaks(best, DRIFT_CHUNK_CUES))
    if drifted is None:
        return best
    return drifted if support(drifted) >= DRIFT_MIN_GAIN * max(1, supports[best]) else best


def drift_scale(scale, peaks):
    # a steady speed error shows as chunk offsets growing along the file; the median of pairwise slopes ignores the jumps of cuts
    trusted = [(moment, offset) for moment, offset, hits, size in peaks if hits >= DRIFT_CHUNK_SUPPORT * size]
    if len(trusted) < DRIFT_MIN_CHUNKS:
        return None
    slopes = [(second[1] - first[1]) / (second[0] - first[0]) for index, first in enumerate(trusted) for second in trusted[index + 1:]
              if second[0] - first[0] > 0]
    slope = float(numpy.median(slopes))
    if not DRIFT_MIN_SLOPE <= abs(slope) <= DRIFT_MAX_SLOPE:
        return None
    return scale * (1 + slope)


def best_offsets(starts, ends, reference, grid, penalty, either):
    step = float(grid[1] - grid[0])
    positions = numpy.arange(len(grid))
    use_ends = len(reference["ends"]) >= MIN_CUES

    def gains(index):
        start_hits = offset_hits(starts[index], reference["starts"], grid)
        if not use_ends:
            hits = start_hits.astype(numpy.float32)
        elif either:
            hits = (start_hits | offset_hits(ends[index], reference["ends"], grid)).astype(numpy.float32)
        else:
            hits = start_hits.astype(numpy.float32) + END_WEIGHT * offset_hits(ends[index], reference["ends"], grid)
        return hits - BEFORE_VIDEO_PENALTY * (starts[index] + grid < 0)

    best = gains(0)
    origins = numpy.zeros((len(starts), len(grid)), dtype=numpy.uint16)
    for index in range(1, len(starts)):
        running_best = numpy.maximum.accumulate(best)
        running_origin = numpy.maximum.accumulate(numpy.where(best == running_best, positions, 0))
        reach = numpy.clip(positions + int((starts[index] - starts[index - 1] + ORDER_SLACK_SECONDS) // step), 0, len(grid) - 1)
        switch = running_best[reach] - penalty
        stays = best >= switch
        origins[index] = numpy.where(stays, positions, running_origin[reach])
        best = numpy.where(stays, best, switch) + gains(index)
    path = [int(numpy.argmax(best))]
    for index in range(len(starts) - 1, 0, -1):
        path.append(int(origins[index, path[-1]]))
    return grid[path[::-1]].astype(float), float(best.max())


def offset_grid(max_offset=MAX_OFFSET_SECONDS, step=GRID_STEP_SECONDS):
    return numpy.arange(-max_offset, max_offset + step / 2, step)


def offset_hits(start, reference, grid):
    step = float(grid[1] - grid[0])
    tolerance_bins = round(TOLERANCE_SECONDS / step)
    first, last = numpy.searchsorted(reference, [start + grid[0] - TOLERANCE_SECONDS, start + grid[-1] + TOLERANCE_SECONDS])
    centres = numpy.rint((reference[first:last] - start - grid[0]) / step).astype(int)
    marks = numpy.zeros(len(grid) + 1, dtype=numpy.int16)
    numpy.add.at(marks, numpy.clip(centres - tolerance_bins, 0, len(grid)), 1)
    numpy.add.at(marks, numpy.clip(centres + tolerance_bins + 1, 0, len(grid)), -1)
    return numpy.cumsum(marks, dtype=numpy.int16)[:-1] > 0


def center_segments(starts, reference, offsets):
    shifts = numpy.arange(-TOLERANCE_SECONDS, TOLERANCE_SECONDS + GRID_STEP_SECONDS / 2, GRID_STEP_SECONDS)
    centred = offsets.copy()
    cues = numpy.arange(len(starts))
    for segment in numpy.split(cues, numpy.flatnonzero(numpy.diff(offsets)) + 1):
        moved = starts[segment] + offsets[segment]
        matched = numpy.array([numpy.abs(nearest_gap(moved + shift, reference)) <= TOLERANCE_SECONDS for shift in shifts], dtype=int)
        totals = numpy.pad(numpy.cumsum(matched, axis=1), ((0, 0), (1, 0)))
        positions = numpy.arange(len(segment))
        neighbours = (totals[:, numpy.minimum(positions + CENTRING_NEIGHBOURS + 1, len(segment))]
                      - totals[:, numpy.maximum(positions - CENTRING_NEIGHBOURS, 0)])
        plateau = neighbours == neighbours.max(axis=0)
        centred[segment] += shifts[numpy.rint((plateau * numpy.arange(len(shifts))[:, None]).sum(axis=0) / plateau.sum(axis=0)).astype(int)]
    for previous, current in itertools.pairwise(cues):
        centred_breaks_order = starts[current] + centred[current] < starts[previous] + centred[previous] - ORDER_SLACK_SECONDS
        if centred_breaks_order and starts[current] + offsets[current] >= starts[previous] + offsets[previous] - ORDER_SLACK_SECONDS:
            centred[[previous, current]] = offsets[[previous, current]]
    return centred
