# SPDX-License-Identifier: GPL-3.0-or-later
"""Which reference line each subtitle line translates, and where each passage of the subtitle belongs, from sentence embeddings.

1. Similarity: each line against each reference line, and groups (a line against two neighbouring reference lines, two lines against one),
   group vectors built from the line vectors, so only lines are embedded. Scores are normalised per row and column (z-scores), so a line
   that resembles everything ("Oui.") earns nothing for resembling one more line.
2. Alignment: one monotonic dynamic programme over 1:1, 1:2 and 2:1 groups, where a subtitle line or a reference line may stay unmatched
   for free (untranslated dialogue, lines the reference lacks). Each matched group keeps its margin over the competing reference lines and
   competing subtitle lines nearby: a match that a neighbour could equally claim is no anchor.
3. Passages: anchors vote for an offset; a Viterbi pass over candidate offsets finds the passages (runs of anchors agreeing on one offset),
   cheaper to split on a dialogue gap. A lone anchor that disagrees with its passage is kept as a contradiction, never erased.
4. Seams: the lines between two passages that the text cannot read belong to one side or the other; the side is chosen only on evidence
   (their own weak matches, how well they read against the reference lines around each place, a start meeting a reference start, speech
   or recognised words under the line, order), otherwise they stay undecided. A line's best match anywhere in the file is never evidence
   for a seam: a phrase said twice ("Your parents,") would pull it across.
5. Gaps: runs of reference dialogue that no subtitle line matches nor faces are untranslated stretches, reported, never filled.
"""

import itertools

import numpy

ROW_KNN_FLOOR = 1e-6
MATCH_FLOOR = 2.5
GROUP_PENALTY = 0.4
NEIGHBOURHOOD = 12
ANCHOR_MARGIN = 0.8
ANCHOR_SCORE = 3.0
SAME_OFFSET = 0.6
LOOSE_SECONDS = 1.5
SPLIT_MARGIN = 1.6
SWITCH_COST = 3.0
GAP_SWITCH_COST = 2.0
DIALOGUE_GAP = 1.5
SHORT_PAUSE = 5.0
LONG_GAP = 25.0
LONG_GAP_RELIEF = 1.5
CONTRADICTION_COST = 1.0
MAX_STATES = 80
PRIOR_WEIGHT = 1.5
PRIOR_BONUS = 0.5
PRIOR_FREE_SECONDS = 2.0
PRIOR_SCALE_SECONDS = 20.0
SENTENCE_ENDS = (".", "!", "?", "…", "。", "\uff01", "\uff1f", '"', "»", ")", "]", "♪")
SENTENCE_GAP = 1.0
SCALES = (1.0, 23.976 / 25, 25 / 23.976, 24 / 25, 25 / 24)
SCALE_MIN_PAIRS = 40
SCALE_WINDOW = 20
SCALE_TOLERANCE = 0.0015
DRIFT_MIN_SECONDS = 0.5
DRIFT_MAX_SLOPE = 0.003
DRIFT_CLOSE = 0.3
DRIFT_CLOSE_SHARE = 0.85
DRIFT_OVER_STEP = 1.5
DRIFT_BLOCK = 12
DRIFT_MIN_BLOCKS = 6
DRIFT_MIN_SPAN = 0.6
COINCIDENCE = 0.25
LOCAL_WINDOW = 2.0
SEAM_MARGIN = 1.0
LOCAL_TEXT_FLOOR = 2.0
LOCAL_TEXT_CAP = 1.5
LISTEN_WEIGHT = 2.0
EDGE_SECONDS = 6.0
WHOLE_FILE_SHARE = 0.8
SPEECH_FRAME = 0.01
GAP_MIN_LINES = 3
GAP_MIN_SECONDS = 6.0
GAP_FACING = 0.5
SOUND_TAG = ("[", "(", "♪", "♫", "#", "*")
MOVE = {"one": 0, "group_reference": 1, "group_lines": 2, "skip_line": 3, "skip_reference": 4}


def align(cues, reference, line_vectors, reference_vectors, speech=None, heard=None, drift=False):
    """cues and reference: [(start, end, text)] sorted by start; vectors: unit rows (zero rows for unreadable lines). heard: optional
    {(line, rounded start): agreement} from recognised speech at a place (text_check.listen_evidence). drift: a slow linear drift may be
    read as a scale (only when something can check it afterwards). Returns the evidence: matched groups, anchors, passages, the offset each
    line would take and how sure that is, and the untranslated stretches."""
    scores = similarities(line_vectors, reference_vectors)
    scores["line_sentences"], scores["reference_sentences"] = sentence_pairs(cues), sentence_pairs(reference)
    groups, scale, anchors, passages = placed_path(scores, cues, reference, drift=drift)
    if PRIOR_WEIGHT and passages:
        # the time prior: the first alignment's passages say where each line may be; a match far from all of them costs, one on them gains
        groups, scale, anchors, passages = placed_path(scores, cues, reference, time_prior(cues, reference, passages, scale), drift)
    lines = assign_lines(cues, reference, groups, passages, scale, scores, (speech, heard or {}))
    return {"scale": scale, "groups": groups, "anchors": anchors, "passages": passages, "lines": lines, "scores": scores["one"], "readable": scores["readable"],
            "matched_references": {other for group in groups for other in group["references"]},
            "gaps": untranslated(cues, reference, groups, lines, scale)}


def placed_path(scores, cues, reference, prior=None, drift=False):
    groups = best_path(scores, prior)
    annotate(groups, scores, cues, reference)
    scale = choose_scale(groups, cues, reference, drift)
    anchors = [group for group in groups if group["anchor"]]
    for group in groups:
        group["offset"] = reference[group["references"][0]][0] - scale * cues[group["lines"][0]][0]
    states = viterbi(anchors, cues)
    return groups, scale, anchors, passages_of(anchors, states)


def time_prior(cues, reference, passages, scale):
    """A soft adjustment per (line, reference line) of the score the alignment sums: a bonus where a reference line starts within SAME_OFFSET
    of where a passage around the line would put it, a cost growing with the distance from all of them, capped so that strong text always
    wins: it never overrides a clear match, it settles weak ones. The offsets are the passages either side of each line, and staying."""
    starts = numpy.array([start for start, _, _ in cues])
    moments = numpy.array([start for start, _, _ in reference])
    offsets = numpy.array([passage["offset"] for passage in passages])
    firsts = numpy.array([passage["first"] for passage in passages])
    after = numpy.minimum(numpy.searchsorted(firsts, numpy.arange(len(cues))), len(passages) - 1)
    before = numpy.maximum(after - 1, 0)
    distance = numpy.abs(moments[None, :] - scale * starts[:, None])
    nearest = numpy.minimum.reduce([numpy.abs(moments[None, :] - (scale * starts[:, None] + offsets[which][:, None])) for which in (before, after)] + [distance])
    cost = PRIOR_WEIGHT * numpy.minimum(1.0, numpy.maximum(0.0, nearest - PRIOR_FREE_SECONDS) / PRIOR_SCALE_SECONDS)
    return numpy.where(nearest <= SAME_OFFSET, PRIOR_BONUS, -cost)


def similarities(line_vectors, reference_vectors):
    readable_lines = numpy.linalg.norm(line_vectors, axis=1) > 0
    raw = line_vectors @ reference_vectors.T
    pair_reference = normalised(reference_vectors[:-1] + reference_vectors[1:])
    pair_lines = normalised(line_vectors[:-1] + line_vectors[1:])
    raw_reference_pairs = line_vectors @ pair_reference.T
    raw_line_pairs = pair_lines @ reference_vectors.T
    one = zscored(raw, readable_lines)
    group_reference = zscored(raw_reference_pairs, readable_lines)
    group_lines = zscored(raw_line_pairs, readable_lines[:-1] & readable_lines[1:])
    return {"raw": raw, "one": one, "group_reference": group_reference, "group_lines": group_lines, "readable": readable_lines}


def sentence_pairs(lines):
    """For each pair of neighbouring lines, 1 when they read as one sentence: the first does not end one and the second follows within
    SENTENCE_GAP seconds (subtitles cut sentences across lines; the pair's vector then stands for the sentence)."""
    pairs = [float(not first[2].rstrip().endswith(SENTENCE_ENDS) and second[0] - first[1] <= SENTENCE_GAP) for first, second in itertools.pairwise(lines)]
    return numpy.array(pairs, dtype=numpy.float32)


def normalised(vectors):
    norms = numpy.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / numpy.maximum(norms, ROW_KNN_FLOOR)


def zscored(raw, readable_rows):
    # half the line's own z-score, half the reference line's: a match must stand out both among the reference lines and among the lines
    if raw.size == 0:
        return raw
    rows = (raw - raw.mean(axis=1, keepdims=True)) / numpy.maximum(raw.std(axis=1, keepdims=True), ROW_KNN_FLOOR)
    usable = raw[readable_rows] if readable_rows.any() else raw
    columns = (raw - usable.mean(axis=0, keepdims=True)) / numpy.maximum(usable.std(axis=0, keepdims=True), ROW_KNN_FLOOR)
    scored = (rows + columns) / 2
    scored[~readable_rows] = -numpy.inf
    return scored


def best_path(scores, prior=None):
    """The monotonic alignment maximising the summed (score - MATCH_FLOOR) of its matched groups; skipping a line or a reference line is
    free, so nothing is matched for lack of a better place. prior: an optional adjustment per (line, reference line), time_prior."""
    one = scores["one"] - MATCH_FLOOR
    # two lines of one sentence (no end of sentence between them, no pause) match as one without the group penalty
    group_reference = scores["group_reference"] - MATCH_FLOOR - GROUP_PENALTY * (1 - scores["reference_sentences"])[None, :]
    group_lines = scores["group_lines"] - MATCH_FLOOR - GROUP_PENALTY * (1 - scores["line_sentences"])[:, None]
    if prior is not None:
        one = one + prior
        group_reference = group_reference + prior[:, :-1]
        group_lines = group_lines + prior[:-1, :]
    count, width = one.shape
    table = numpy.zeros((count + 1, width + 1))
    moves = numpy.full((count + 1, width + 1), MOVE["skip_line"], dtype=numpy.int8)
    moves[0, :] = MOVE["skip_reference"]
    for line in range(1, count + 1):
        options = numpy.full((4, width + 1), -numpy.inf)
        options[MOVE["skip_line"]] = table[line - 1]
        options[MOVE["one"], 1:] = table[line - 1, :-1] + one[line - 1]
        if width >= 2:
            options[MOVE["group_reference"], 2:] = table[line - 1, :-2] + group_reference[line - 1]
        if line >= 2:
            options[MOVE["group_lines"], 1:] = table[line - 2, :-1] + group_lines[line - 2]
        choice = options.argmax(axis=0)
        best = options[choice, numpy.arange(width + 1)]
        running = numpy.maximum.accumulate(best)
        moves[line] = numpy.where(running > best, MOVE["skip_reference"], choice)
        table[line] = running
    return trace(moves, scores, count, width)


def trace(moves, scores, line, other):
    groups = []
    while line > 0 and other > 0:
        move = moves[line, other]
        if move == MOVE["skip_reference"]:
            other -= 1
        elif move == MOVE["skip_line"]:
            line -= 1
        elif move == MOVE["one"]:
            groups.append({"lines": (line - 1,), "references": (other - 1,), "score": float(scores["one"][line - 1, other - 1])})
            line, other = line - 1, other - 1
        elif move == MOVE["group_reference"]:
            groups.append({"lines": (line - 1,), "references": (other - 2, other - 1), "score": float(scores["group_reference"][line - 1, other - 2])})
            line, other = line - 1, other - 2
        else:
            groups.append({"lines": (line - 2, line - 1), "references": (other - 1,), "score": float(scores["group_lines"][line - 2, other - 1])})
            line, other = line - 2, other - 1
    return groups[::-1]


def annotate(groups, scores, cues, reference):
    """Each group's margin: its score minus the best competing reference line for its lines and the best competing line for its reference
    lines, within NEIGHBOURHOOD; an anchor is a group whose margin and score both stand out."""
    one = scores["one"]
    count, width = one.shape
    for group in groups:
        first_line, last_line = group["lines"][0], group["lines"][-1]
        first_reference, last_reference = group["references"][0], group["references"][-1]
        row = one[first_line, max(0, first_reference - NEIGHBOURHOOD):min(width, last_reference + NEIGHBOURHOOD + 1)].copy()
        row[first_reference - max(0, first_reference - NEIGHBOURHOOD):last_reference - max(0, first_reference - NEIGHBOURHOOD) + 1] = -numpy.inf
        column = one[max(0, first_line - NEIGHBOURHOOD):min(count, last_line + NEIGHBOURHOOD + 1), first_reference].copy()
        column[first_line - max(0, first_line - NEIGHBOURHOOD):last_line - max(0, first_line - NEIGHBOURHOOD) + 1] = -numpy.inf
        competitor = max(float(row.max()) if row.size else -numpy.inf, float(column.max()) if column.size else -numpy.inf)
        group["margin"] = group["score"] - competitor if numpy.isfinite(competitor) else group["score"]
        group["similarity"] = float(scores["raw"][first_line, first_reference])
        group["words"] = min(len(cues[line][2].split()) for line in group["lines"])
        group["anchor"] = group["margin"] >= ANCHOR_MARGIN and group["score"] >= ANCHOR_SCORE


def choose_scale(groups, cues, reference, drift=False):
    """A frame-rate mismatch stretches the whole file: the scale under which consecutive anchors keep the same offset most often. With
    drift on, a slope too small to be a frame rate may still be a slow drift (linear_drift)."""
    pairs = [(cues[group["lines"][0]][0], reference[group["references"][0]][0]) for group in groups if group["anchor"]]
    if len(pairs) < SCALE_MIN_PAIRS:
        return 1.0
    starts, moments = numpy.array(pairs).T
    # the median slope of the offset between anchors SCALE_WINDOW apart: cuts and passages bend a few pairs, a frame rate bends them all
    slopes = (moments[SCALE_WINDOW:] - moments[:-SCALE_WINDOW]) / numpy.maximum(starts[SCALE_WINDOW:] - starts[:-SCALE_WINDOW], 1e-6)
    measured = float(numpy.median(slopes))
    nearest = min(SCALES, key=lambda scale: abs(scale - measured))
    if drift and nearest == 1.0:
        return linear_drift(starts, moments)
    return nearest if abs(nearest - measured) <= SCALE_TOLERANCE else 1.0


def linear_drift(starts, moments):
    """The scale of a slow drift, or 1: blocks of anchors (their medians, single anchors are too noisy) lie on one line across most of the file, close enough to it, the drift over the file is worth correcting,
    and that line explains them better than a single step would (a cut bends the median slope the same way)."""
    offsets = moments - starts
    blocks = range(0, len(starts) - DRIFT_BLOCK + 1, DRIFT_BLOCK)
    times = numpy.array([numpy.median(starts[first:first + DRIFT_BLOCK]) for first in blocks])
    levels = numpy.array([numpy.median(offsets[first:first + DRIFT_BLOCK]) for first in blocks])
    half = len(times) // 2
    span = float(starts[-1] - starts[0])
    if len(times) < DRIFT_MIN_BLOCKS or span < DRIFT_MIN_SPAN * float(starts[-1]):
        return 1.0
    slope = float(numpy.median((levels[half:] - levels[:len(times) - half]) / numpy.maximum(times[half:] - times[:len(times) - half], 1e-6)))
    if abs(slope) > DRIFT_MAX_SLOPE or abs(slope) * span < DRIFT_MIN_SECONDS:
        return 1.0
    residual = levels - slope * times
    residual = residual - numpy.median(residual)
    if numpy.mean(numpy.abs(residual) <= DRIFT_CLOSE) < DRIFT_CLOSE_SHARE:
        return 1.0
    return 1.0 + slope if best_step_error(levels) >= DRIFT_OVER_STEP * float(numpy.sum(residual ** 2)) else 1.0


def best_step_error(values):
    # the least squared error of a one-step function over these values
    totals, squares = numpy.cumsum(values), numpy.cumsum(values ** 2)
    sizes = numpy.arange(1, len(values))
    left = squares[:-1] - totals[:-1] ** 2 / sizes
    right_total = totals[-1] - totals[:-1]
    right = squares[-1] - squares[:-1] - right_total ** 2 / (len(values) - sizes)
    return float(numpy.min(left + right))


def viterbi(anchors, cues):
    """The offset each anchor's passage has: candidate offsets are clusters of anchor offsets; agreeing costs nothing, disagreeing costs the
    anchor's weight, changing offset costs SWITCH_COST (less across a dialogue gap)."""
    if not anchors:
        return []
    offsets = numpy.array([anchor["offset"] for anchor in anchors])
    states = candidate_offsets(offsets)
    weights = numpy.array([min(1.0, anchor["margin"] / (2 * ANCHOR_MARGIN)) for anchor in anchors]) * CONTRADICTION_COST
    emission = numpy.where(numpy.abs(offsets[:, None] - states[None, :]) <= SAME_OFFSET, 0.0, weights[:, None])
    cost = emission[0].copy()
    back = numpy.zeros((len(anchors), len(states)), dtype=numpy.int32)
    for index in range(1, len(anchors)):
        switch = switch_cost(largest_gap(cues, anchors[index - 1]["lines"][-1], anchors[index]["lines"][0]))
        best_previous = int(cost.argmin())
        stay = cost
        move = cost[best_previous] + switch
        back[index] = numpy.where(stay <= move, numpy.arange(len(states)), best_previous)
        cost = numpy.minimum(stay, move) + emission[index]
    path = [int(cost.argmin())]
    for index in range(len(anchors) - 1, 0, -1):
        path.append(int(back[index, path[-1]]))
    path = path[::-1]
    chosen = []
    for anchor, state in zip(anchors, path, strict=True):
        anchor["agrees"] = abs(anchor["offset"] - states[state]) <= SAME_OFFSET
        chosen.append(state)
    return [refined(anchors, chosen, state, states) for state in chosen]


def candidate_offsets(offsets):
    ordered = numpy.sort(offsets)
    clusters, current = [], [ordered[0]]
    for value in ordered[1:]:
        if value - current[0] <= SAME_OFFSET:
            current.append(value)
            continue
        clusters.append(current)
        current = [value]
    clusters.append(current)
    supported = sorted((cluster for cluster in clusters if len(cluster) >= 2), key=len, reverse=True)[:MAX_STATES] or clusters[:1]
    return numpy.array([float(numpy.median(cluster)) for cluster in supported])


def refined(anchors, chosen, state, states):
    # the passage's offset is the median of the anchors that agree with its state, so it follows the data rather than the cluster grid
    del anchors, chosen
    return float(states[state])


def largest_gap(cues, first, last):
    return max((cues[line + 1][0] - cues[line][1] for line in range(first, last)), default=0.0)


def switch_cost(gap):
    """Changing offset is cheaper across a dialogue gap, and cheaper still across a long silence (a cut, a scene change): a passage boundary
    goes where the subtitle itself pauses, unless the anchors insist otherwise."""
    if gap < DIALOGUE_GAP:
        return SWITCH_COST
    return GAP_SWITCH_COST - LONG_GAP_RELIEF * min(1.0, max(0.0, gap - SHORT_PAUSE) / LONG_GAP)


def passages_of(anchors, states):
    """Runs of consecutive anchors sharing one state, with the median offset of the anchors that agree with it. A contradicting anchor
    with a clear margin whose own offset is that of the passage before or after splits its passage: two passages interleave there (a line left in
    place among moved ones), and the lines around it become seams to settle on evidence rather than lines dragged along."""
    runs = []
    for anchor, state in zip(anchors, states, strict=True):
        if runs and runs[-1][0] == state:
            runs[-1][1].append(anchor)
            continue
        runs.append((state, [anchor]))
    passages = [summarised(members, state) for state, members in runs]
    split = []
    for number, passage in enumerate(passages):
        others = [passages[index]["offset"] for index in (number - 1, number + 1) if 0 <= index < len(passages)]
        current = []
        for anchor in passage["anchors"]:
            own = next((other for other in others if abs(anchor["offset"] - other) <= SAME_OFFSET), None)
            if anchor["agrees"] or own is None or abs(own - passage["offset"]) <= SAME_OFFSET or anchor["margin"] < SPLIT_MARGIN:
                current.append(anchor)
                continue
            if current:
                split.append(summarised(current, passage["state"]))
            anchor["agrees"] = True
            split.append(summarised([anchor], own))
            current = []
        if current:
            split.append(summarised(current, passage["state"]))
    return split


def summarised(members, state):
    # a passage spans its agreeing anchors only: a contradicted anchor at its edge is a seam line, not a member
    agreeing = [anchor for anchor in members if anchor["agrees"]]
    bounds = agreeing or members
    return {"state": state, "anchors": members, "offset": float(numpy.median([anchor["offset"] for anchor in agreeing])) if agreeing else state,
            "first": bounds[0]["lines"][0], "last": bounds[-1]["lines"][-1], "support": len(agreeing),
            "contradictions": [anchor for anchor in members if not anchor["agrees"]]}


def assign_lines(cues, reference, groups, passages, scale, scores, audio):
    """Per line: the passage offset it takes and how it is known: anchored (its own anchor agrees), inside (between two anchors of its
    passage), seam (between two passages, side chosen on evidence), edge (before the first or after the last anchor, close to it),
    loose (its own anchor is off the passage by under LOOSE_SECONDS: translations start a little before or after the reference),
    contradiction (its own anchor puts it further) or undecided."""
    lines = [{"offset": None, "how": "undecided", "passage": None} for _ in cues]
    by_line = {line: group for group in groups for line in group["lines"]}
    matched = {other for group in groups for other in group["references"]}
    starts = numpy.array([start for start, _, _ in reference])
    for number, passage in enumerate(passages):
        for anchor in passage["anchors"]:
            if not passage["first"] <= anchor["lines"][0] <= passage["last"]:
                continue
            for line in anchor["lines"]:
                how = "anchored" if anchor["agrees"] else "loose" if within_match(cues, reference, anchor, line, passage["offset"], scale, matched) else "contradiction"
                lines[line] = {"offset": passage["offset"], "how": how, "passage": number, "own_offset": anchor["offset"], "margin": anchor["margin"]}
        for line in range(passage["first"], passage["last"] + 1):
            if lines[line]["how"] == "undecided":
                lines[line] = {"offset": passage["offset"], "how": "inside", "passage": number}
    for number in range(len(passages) - 1):
        settle_seam(cues, lines, passages, number, by_line, starts, scale, scores, audio)
    if passages:
        settle_edge(cues, lines, passages, 0, range(passages[0]["first"] - 1, -1, -1), by_line, starts, scale, scores, audio)
        settle_edge(cues, lines, passages, len(passages) - 1, range(passages[-1]["last"] + 1, len(cues)), by_line, starts, scale, scores, audio)
    out_of_order(cues, reference, lines, by_line, scale, scores)
    return lines


def out_of_order(cues, reference, lines, by_line, scale, scores):
    """A readable line the monotonic alignment left out, whose own clear best match puts it elsewhere, is a contradiction: a line moved past
    its neighbours cannot be matched in order, and silence about it must not read as agreement."""
    one = scores["one"]
    for line, state in enumerate(lines):
        if line in by_line or state["offset"] is None or not scores["readable"][line]:
            continue
        row = one[line]
        best = int(numpy.argmax(row))
        others = row.copy()
        others[max(0, best - 1):best + 2] = -numpy.inf
        margin = float(row[best] - others.max()) if numpy.isfinite(others.max()) else float(row[best])
        if row[best] < ANCHOR_SCORE or margin < ANCHOR_MARGIN:
            continue
        own = reference[best][0] - scale * cues[line][0]
        placed = scale * cues[line][0] + state["offset"]
        if abs(own - state["offset"]) <= LOOSE_SECONDS or reference[best][0] - SAME_OFFSET <= placed < reference[best][1]:
            continue
        lines[line] = {**state, "how": "contradiction", "own_offset": own, "margin": margin, "unmatched": True}


def within_match(cues, reference, anchor, line, offset, scale, matched):
    """A line off its anchor's start can still sit on the reference line it translates: the second half of a long sentence starts inside
    it, and a line translating two reference lines matched only to the second starts on the first (left unmatched). It is loose, not
    contradicted, while it starts within LOOSE_SECONDS of that start or inside the matched span, widened to an unmatched line before."""
    placed = scale * cues[line][0] + offset
    first_index = anchor["references"][0]
    if first_index > 0 and first_index - 1 not in matched:
        first_index -= 1
    first, last = reference[first_index], reference[anchor["references"][-1]]
    return abs(anchor["offset"] - offset) <= LOOSE_SECONDS or first[0] - SAME_OFFSET <= placed < last[1]


def settle_seam(cues, lines, passages, number, by_line, starts, scale, scores, audio):
    """The lines between passage `number` and the next: one cut point splits them between the two offsets. Each cut is scored by the evidence
    of every line on the side it puts it; the best cut is kept only if it beats every cut that places some line differently by SEAM_MARGIN,
    otherwise the lines the plausible cuts disagree on stay undecided."""
    left, right = passages[number], passages[number + 1]
    between = list(range(left["last"] + 1, right["first"]))
    if not between:
        return
    if abs(left["offset"] - right["offset"]) <= SAME_OFFSET:
        for line in between:
            lines[line] = {"offset": left["offset"], "how": "inside", "passage": number}
        return
    evidence = [(line_evidence(cues, line, left["offset"], by_line, starts, scale, scores, audio),
                 line_evidence(cues, line, right["offset"], by_line, starts, scale, scores, audio)) for line in between]
    cuts = []
    for cut in range(len(between) + 1):
        total = sum(left_side for left_side, _ in evidence[:cut]) + sum(right_side for _, right_side in evidence[cut:])
        if not ordered_cut(cues, between, cut, left, right, scale):
            total = -numpy.inf
        cuts.append(total)
    best = int(numpy.argmax(cuts))
    if not numpy.isfinite(cuts[best]):
        return
    rivals = [cut for cut, total in enumerate(cuts) if cut != best and total > cuts[best] - SEAM_MARGIN]
    low, high = min([best, *rivals]), max([best, *rivals])
    for position, line in enumerate(between):
        if low <= position < high:
            continue
        side, number_of = (left, number) if position < best else (right, number + 1)
        lines[line] = {"offset": side["offset"], "how": "seam", "passage": number_of, "seam_margin": cuts[best] - max((cuts[rival] for rival in rivals),
                                                                                                                   default=cuts[best] - 2 * SEAM_MARGIN)}


def ordered_cut(cues, between, cut, left, right, scale):
    # a cut that would put a line before the line preceding it, once both are moved, is impossible
    moved = [scale * cues[line][0] + (left["offset"] if position < cut else right["offset"]) for position, line in enumerate(between)]
    before = scale * cues[left["last"]][0] + left["offset"]
    after = scale * cues[right["first"]][0] + right["offset"]
    sequence = [before, *moved, after]
    return all(later >= earlier - COINCIDENCE for earlier, later in itertools.pairwise(sequence))


def settle_edge(cues, lines, passages, number, order, by_line, starts, scale, scores, audio):
    """Lines before the first anchor or after the last one follow the passage beside them only while close to it and with evidence for it.
    When that passage, with the passages next to it sharing its offset, spans most of the file (a whole-file offset or frame rate), they
    follow it anyway, marked unsupported: leaving them behind would break the order, and the passage's safety weighs them."""
    passage = passages[number]
    anchor_line = passage["first"] if order and order.step == -1 else passage["last"]
    whole_file = chain_span(passages, number) >= WHOLE_FILE_SHARE * len(cues)
    for line in order:
        if lines[line]["how"] != "undecided":
            return
        close = abs(cues[line][0] - cues[anchor_line][0]) <= EDGE_SECONDS
        if close and line_evidence(cues, line, passage["offset"], by_line, starts, scale, scores, audio) >= SEAM_MARGIN:
            lines[line] = {"offset": passage["offset"], "how": "edge", "passage": number}
            continue
        if not whole_file:
            return
        lines[line] = {"offset": passage["offset"], "how": "edge", "passage": number, "unsupported": True}


def chain_span(passages, number):
    # lines covered by the passage and its neighbours on the same offset (a lone contradiction can split one offset into two passages)
    offset, first, last = passages[number]["offset"], number, number
    while first > 0 and abs(passages[first - 1]["offset"] - offset) <= SAME_OFFSET:
        first -= 1
    while last + 1 < len(passages) and abs(passages[last + 1]["offset"] - offset) <= SAME_OFFSET:
        last += 1
    return passages[last]["last"] - passages[first]["first"] + 1


def line_evidence(cues, line, offset, by_line, starts, scale, scores, audio):
    """How much a line supports sitting at `offset`: its own (weak) match agreeing, how well it reads against the reference lines around
    that place, its start meeting a reference start there, speech under it there, recognised words there agreeing with it. Each piece is
    small; none is proof."""
    speech, heard = audio
    start, end, _ = cues[line]
    moved_start, moved_end = scale * start + offset, scale * end + offset
    total = 0.0
    group = by_line.get(line)
    if group is not None:
        agree = abs(group["offset"] - offset) <= SAME_OFFSET
        total += (1.0 if agree else -1.0) * min(2.0, max(0.0, group["margin"]) + 0.5)
    if scores["readable"][line]:
        local, _ = local_score(scores["one"], starts, line, moved_start, moved_end)
        total += min(LOCAL_TEXT_CAP, max(-1.0, local - LOCAL_TEXT_FLOOR))
    if starts.size and numpy.min(numpy.abs(starts - moved_start)) <= COINCIDENCE:
        total += 1.0
    if speech is not None:
        total += 2.0 * (speech_share(speech, moved_start, moved_end) - 0.5)
    agreement = heard.get((line, round(moved_start, 1)))
    if agreement is not None:
        total += LISTEN_WEIGHT * agreement
    return total


def local_score(scores, reference_starts, line, start, end):
    """The line's best normalised score against the reference lines starting around where it would be shown: text support for a place,
    whatever the alignment chose."""
    low, high = numpy.searchsorted(reference_starts, [start - LOCAL_WINDOW, end + LOCAL_WINDOW])
    if high <= low or not numpy.isfinite(scores[line, 0]):
        return 0.0, None
    best = int(numpy.argmax(scores[line, low:high]))
    return float(scores[line, low + best]), low + best


def speech_share(speech, start, end):
    # 0.5 (no opinion) where nothing was read: a mask read only around seams is NaN elsewhere
    first, last = int(max(start, 0) / SPEECH_FRAME), int(max(end, 0) / SPEECH_FRAME)
    if last <= first or first >= len(speech):
        return 0.5
    window = numpy.asarray(speech[first:min(last, len(speech))], dtype=numpy.float32)
    known = window[~numpy.isnan(window)]
    return float(known.mean()) if known.size else 0.5


def untranslated(cues, reference, groups, lines, scale):
    """Reference dialogue no subtitle line translates nor faces once placed: runs of unmatched reference lines from GAP_MIN_LINES lines and
    GAP_MIN_SECONDS, with no subtitle line starting inside them. Returned as (from, to, reference lines) in reference seconds."""
    matched = {other for group in groups for other in group["references"]}
    placed = sorted((scale * start + (state["offset"] or 0.0), scale * end + (state["offset"] or 0.0))
                    for (start, end, _), state in zip(cues, lines, strict=True))
    placed_starts = numpy.array([start for start, _ in placed]) if placed else numpy.zeros(0)
    placed_ends = numpy.array([end for _, end in placed]) if placed else numpy.zeros(0)
    gaps, current = [], []
    for index, (start, end, text) in enumerate(reference):
        faced = placed_starts.size and bool(numpy.any((placed_starts < end + GAP_FACING) & (placed_ends > start - GAP_FACING)))
        if index in matched or faced or text.strip().startswith(SOUND_TAG):
            if not faced and index not in matched:
                continue
            gaps.append(current)
            current = []
            continue
        if current and placed_starts.size and numpy.any((placed_starts > reference[current[-1]][1]) & (placed_starts < start)):
            gaps.append(current)
            current = []
        current.append(index)
    gaps.append(current)
    return [(reference[run[0]][0], reference[run[-1]][1], len(run)) for run in gaps
            if len(run) >= GAP_MIN_LINES and reference[run[-1]][1] - reference[run[0]][0] >= GAP_MIN_SECONDS]
