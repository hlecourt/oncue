"""Score a subtitle against a reference, realign it piecewise and decide whether the result can be trusted."""

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
WEAK_WINDOW_MATCHED = 0.4
WEAK_WINDOW_SECONDS = 60
WEAK_WINDOW_STEP_SECONDS = 30
NOISE_OFFSETS = (-97.0, -53.0, 37.0, 71.0, 113.0)
SCALES = (1.0, 25 / 23.976, 23.976 / 25)
SCALE_CHUNK_CUES = 30
GRID_STEP_SECONDS = 0.05
MAX_OFFSET_SECONDS = 400.0
WIDE_MAX_OFFSET_SECONDS = 1200.0
WIDE_GRID_STEP_SECONDS = 0.1
SWITCH_PENALTY = 2.5
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
MAX_WRONG_END_SHARE = 0.05


def wrong_end_share(cues):
    if not cues:
        return 0.0
    return sum(end < start or end - start > MAX_CUE_SECONDS for start, end, _ in cues) / len(cues)


def judge(cues, reference):
    original = score(numpy.array([start for start, _, _ in cues]), reference["starts"])
    weak = weak_minutes(cues, reference)
    if original["passed"] and not weak:
        return DECISION["kept"], original, weak, None
    attempt = assess(cues, reference)
    better = attempt["fixed"] and len(attempt["weak_minutes"]) < len(weak) and attempt["score"]["matched"] >= original["matched"]
    if original["passed"] and not better:
        return DECISION["kept"], original, weak, attempt
    return (DECISION["fixed"] if attempt["fixed"] else DECISION["rejected"]), original, weak, attempt


def describe_changes(cues, attempt):
    scale = attempt.get("scale", 1.0)
    offsets = [new_start - start * scale for (new_start, _, _), (start, _, _) in zip(attempt["timings"], cues)]
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
    return parts + [{"first": cues[members[0]][0], "last": cues[members[-1]][0], "lines": len(members),
                     "shift": round(float(numpy.median([shifts[member] for member in members])), 1)}]


def assess(cues, reference, wide_retry=True):
    attempt = assess_on_grid(cues, reference, offset_grid())
    if attempt["fixed"] or not wide_retry:
        return attempt
    return {**assess_on_grid(cues, reference, offset_grid(WIDE_MAX_OFFSET_SECONDS, WIDE_GRID_STEP_SECONDS)), "wide_search": True}


def assess_on_grid(cues, reference, grid):
    mirrored = mirror(reference)
    with ThreadPoolExecutor(max_workers=1) as pool:
        null_alignment = pool.submit(align, cues, mirrored, grid)
        aligned = align(cues, reference, grid)
        null = null_alignment.result()
    aligned_score = score(numpy.array([start for start, _, _ in aligned["timings"]]), reference["starts"])
    null_score = score(numpy.array([start for start, _, _ in null["timings"]]), mirrored["starts"])
    fixed = (aligned_score is not None and aligned_score["passed"] and aligned["objective"] >= null["objective"] + OBJECTIVE_MARGIN
             and aligned["unsupported_share"] <= MAX_UNSUPPORTED_SHARE)
    return {"timings": aligned["timings"], "score": aligned_score, "null": null_score, "fixed": bool(fixed), "scale": aligned["scale"],
            "objective": aligned["objective"], "null_objective": null["objective"], "segments": aligned["segments"],
            "null_segments": null["segments"], "short_segment_share": aligned["short_segment_share"],
            "unsupported_share": aligned["unsupported_share"],
            "weak_minutes": weak_minutes(aligned["timings"], reference)}


def mirror(reference):
    end_of_video = max(reference["starts"].max(), reference["ends"].max() if len(reference["ends"]) else 0) + 1
    if len(reference["ends"]) < MIN_CUES:
        return {"kind": reference["kind"], "starts": numpy.sort(end_of_video - reference["starts"]), "ends": numpy.array([])}
    return {"kind": reference["kind"], "starts": numpy.sort(end_of_video - reference["ends"]), "ends": numpy.sort(end_of_video - reference["starts"])}


def score(starts, reference):
    if len(starts) < MIN_CUES:
        return None
    starts = numpy.sort(starts)
    matched = matched_fraction(starts, reference, 1.0, 0.0)
    noise = float(numpy.mean([matched_fraction(starts, reference, 1.0, offset) for offset in NOISE_OFFSETS]))
    windows = [matched_fraction(inside, reference, 1.0, 0.0) for inside in windows_of(starts) if len(inside) >= MIN_WINDOW_CUES]
    worst_window = min(windows) if windows else matched
    passed = matched >= noise + PASS_MARGIN and worst_window >= noise + WINDOW_PASS_MARGIN
    return {"matched": round(matched, 2), "noise": round(noise, 2), "worst_window": round(worst_window, 2), "passed": bool(passed)}


def weak_minutes(cues, reference):
    starts = numpy.sort(numpy.array([start for start, _, _ in cues]))
    weak = set()
    for window_start in numpy.arange(0, starts[-1] + 1, WEAK_WINDOW_STEP_SECONDS):
        inside = starts[(starts >= window_start) & (starts < window_start + WEAK_WINDOW_SECONDS)]
        if len(inside) >= MIN_WINDOW_CUES and matched_fraction(inside, reference["starts"], 1.0, 0.0) < WEAK_WINDOW_MATCHED:
            weak.add(int(window_start // 60))
    return sorted(weak)


def windows_of(starts):
    return [starts[(starts >= window_start) & (starts < window_start + WINDOW_SECONDS)] for window_start in numpy.arange(0, starts[-1] + 1, WINDOW_SECONDS)]


def matched_fraction(starts, reference, scale, offset):
    return float(numpy.mean(numpy.abs(nearest_gap(starts * scale + offset, reference)) <= TOLERANCE_SECONDS))


def nearest_gap(moved, reference):
    positions = numpy.clip(numpy.searchsorted(reference, moved), 1, len(reference) - 1)
    after, before = reference[positions] - moved, reference[positions - 1] - moved
    return numpy.where(numpy.abs(after) < numpy.abs(before), after, before)


def align_piecewise(cues, reference):
    return align(cues, reference)["timings"]


def align(cues, reference, grid=None):
    grid = offset_grid() if grid is None else grid
    order = numpy.argsort([start for start, _, _ in cues], kind="stable")
    starts = numpy.array([cues[index][0] for index in order])
    ends = numpy.array([cues[index][1] for index in order])
    scale = choose_scale(starts, reference["starts"], grid)
    offsets, objective, lengths = best_offsets(starts * scale, ends * scale, reference, grid)
    timings = [None] * len(cues)
    for rank, index in enumerate(order):
        timings[index] = (starts[rank] * scale + offsets[rank], ends[rank] * scale + offsets[rank], cues[index][2])
    hits = numpy.abs(nearest_gap(starts * scale + offsets, reference["starts"])) <= TOLERANCE_SECONDS
    if len(reference["ends"]) >= MIN_CUES:
        hits |= numpy.abs(nearest_gap(ends * scale + offsets, reference["ends"])) <= TOLERANCE_SECONDS
    support = [segment.mean() for segment in numpy.split(hits, numpy.cumsum(lengths)[:-1])]
    unsupported = sum(length for length, matched in zip(lengths, support) if matched < SUPPORTED_SEGMENT_MATCHED)
    return {"timings": timings, "objective": objective / len(cues), "segments": len(lengths), "scale": float(scale),
            "short_segment_share": float(lengths[lengths < SHORT_SEGMENT_CUES].sum() / len(cues)),
            "unsupported_share": float(unsupported / len(cues))}


def choose_scale(starts, reference, grid):
    def support(scale):
        total = 0
        for chunk_start in range(0, len(starts), SCALE_CHUNK_CUES):
            hits = sum(offset_hits(start * scale, reference, grid).astype(numpy.int16) for start in starts[chunk_start:chunk_start + SCALE_CHUNK_CUES])
            total += int(hits.max())
        return total

    return max(SCALES, key=support)


def best_offsets(starts, ends, reference, grid):
    step = float(grid[1] - grid[0])
    positions = numpy.arange(len(grid))
    use_ends = len(reference["ends"]) >= MIN_CUES

    def gains(index):
        hits = offset_hits(starts[index], reference["starts"], grid).astype(numpy.float32)
        if use_ends:
            hits += END_WEIGHT * offset_hits(ends[index], reference["ends"], grid)
        return hits - BEFORE_VIDEO_PENALTY * (starts[index] + grid < 0)

    best = gains(0)
    origins = numpy.zeros((len(starts), len(grid)), dtype=numpy.uint16)
    for index in range(1, len(starts)):
        running_best = numpy.maximum.accumulate(best)
        running_origin = numpy.maximum.accumulate(numpy.where(best == running_best, positions, 0))
        reach = numpy.clip(positions + int((starts[index] - starts[index - 1] + ORDER_SLACK_SECONDS) // step), 0, len(grid) - 1)
        switch = running_best[reach] - SWITCH_PENALTY
        stays = best >= switch
        origins[index] = numpy.where(stays, positions, running_origin[reach])
        best = numpy.where(stays, best, switch) + gains(index)
    path = [int(numpy.argmax(best))]
    for index in range(len(starts) - 1, 0, -1):
        path.append(int(origins[index, path[-1]]))
    offsets = grid[path[::-1]]
    lengths = numpy.diff(numpy.flatnonzero(numpy.concatenate(([True], numpy.diff(offsets) != 0, [True]))))
    return center_segments(starts, reference["starts"], offsets), float(best.max()), lengths


def offset_grid(max_offset=MAX_OFFSET_SECONDS, step=GRID_STEP_SECONDS):
    return numpy.arange(-max_offset, max_offset + step / 2, step)


def offset_hits(start, reference, grid):
    step = float(grid[1] - grid[0])
    tolerance_bins = int(round(TOLERANCE_SECONDS / step))
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
    for previous, current in zip(cues[:-1], cues[1:]):
        centred_breaks_order = starts[current] + centred[current] < starts[previous] + centred[previous] - ORDER_SLACK_SECONDS
        if centred_breaks_order and starts[current] + offsets[current] >= starts[previous] + offsets[previous] - ORDER_SLACK_SECONDS:
            centred[[previous, current]] = offsets[[previous, current]]
    return centred
