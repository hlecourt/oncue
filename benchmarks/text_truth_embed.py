# SPDX-License-Identifier: GPL-3.0-or-later
"""An engine for text_truth.py that checks timing by text: each subtitle line is matched to the reference line it translates with a
multilingual sentence embedding and a monotonic chain, and a passage moves only when several consecutive matches agree on the same offset.

ONCUE_EMBED_MODE: "hybrid" (default) starts from OnCue's own result and corrects or downgrades it, "text" uses the text matches alone.
ONCUE_EMBED_MODEL: a sentence-transformers model, LaBSE by default. Needs sentence-transformers (not an OnCue dependency).
"""
import itertools
import os
import re

import numpy
import text_truth_oncue

MODEL_NAME = os.environ.get("ONCUE_EMBED_MODEL", "sentence-transformers/LaBSE")
MODE = os.environ.get("ONCUE_EMBED_MODE", "hybrid")
CANDIDATES = 5
MIN_SIMILARITY = 0.45
AGREE_SECONDS = 0.7
MAX_ANCHOR_GAP_LINES = 4
MOVE_FROM_SECONDS = 2.5
MIN_RUN = 2
STRONG_RUN = 3
VERIFIED_COVERAGE = 0.6
KEEP_WITHIN_SECONDS = 0.6
MAX_LOOSE_SHARE = 0.015
STATE = {}


def run(cues, tracks, hearing_impaired, texts):
    base = text_truth_oncue.run(cues, tracks, hearing_impaired, texts) if MODE == "hybrid" else {"decision": "kept", "confidence": 0, "timings": None}
    reference = [(start, end, clean(text)) for start, end, text in texts or [] if clean(text)]
    if len(reference) < 20:
        return {**base, "detail": "no reference text"}
    start_timings = base["timings"] or [(start, end, text) for start, end, text in cues]
    similarity = embed([clean(text) for _, _, text in cues]) @ embed([text for _, _, text in reference]).T
    anchors = chain(similarity)
    estimates, runs = run_offsets(start_timings, reference, anchors)
    timings = [(start + estimate, end + estimate, text) if estimate is not None and abs(estimate) > MOVE_FROM_SECONDS else (start, end, text)
               for (start, end, text), estimate in zip(start_timings, estimates, strict=True)]
    corrected = [run_lines for run_lines, offset in runs if abs(offset) > MOVE_FROM_SECONDS]
    coverage = sum(estimate is not None for estimate in estimates) / len(cues)
    loose = sum(estimate is not None and KEEP_WITHIN_SECONDS < abs(estimate) <= MOVE_FROM_SECONDS for estimate in estimates) / len(cues)
    moved = any(abs(new[0] - old[0]) > 0.5 for new, old in zip(timings, cues, strict=True))
    if not corrected and coverage >= VERIFIED_COVERAGE and loose <= MAX_LOOSE_SHARE:
        confidence = max(base["confidence"], 90)
    elif corrected and coverage >= VERIFIED_COVERAGE and loose <= MAX_LOOSE_SHARE and all(count >= STRONG_RUN for count in corrected):
        confidence = 90
    else:
        confidence = min(base["confidence"], 75)
    return {"decision": "fixed" if moved else "kept", "confidence": confidence, "timings": timings if moved else None,
            "detail": {"coverage": round(coverage, 2), "loose": round(loose, 3), "corrected_runs": len(corrected), "base": base["decision"], "base_confidence": base["confidence"]}}


def embed(texts):
    if "model" not in STATE:
        from sentence_transformers import SentenceTransformer
        STATE["model"] = SentenceTransformer(MODEL_NAME, device="cpu")
        STATE["cache"] = {}
    prefix = "query: " if "e5" in MODEL_NAME else ""
    missing = [text for text in dict.fromkeys(texts) if text not in STATE["cache"]]
    if missing:
        for text, vector in zip(missing, STATE["model"].encode([prefix + text for text in missing], batch_size=64, normalize_embeddings=True), strict=True):
            STATE["cache"][text] = vector
    return numpy.array([STATE["cache"][text] for text in texts])


def chain(similarity):
    """Best monotonic chain (subtitle index increasing, reference index non-decreasing) over each line's top candidates, by summed similarity."""
    top = numpy.argsort(-similarity, axis=1)[:, :CANDIDATES]
    pairs = [(line, int(other), float(similarity[line, other])) for line in range(similarity.shape[0]) for other in top[line]
             if similarity[line, other] >= MIN_SIMILARITY]
    size = similarity.shape[1] + 1
    tree_value, tree_index = [0.0] * (size + 1), [-1] * (size + 1)
    best, previous = [0.0] * len(pairs), [-1] * len(pairs)
    by_line = {}
    for index, pair in enumerate(pairs):
        by_line.setdefault(pair[0], []).append(index)
    for line in sorted(by_line):
        updates = []
        for index in by_line[line]:
            position, top_value, top_index = pairs[index][1] + 1, 0.0, -1
            while position > 0:
                if tree_value[position] > top_value:
                    top_value, top_index = tree_value[position], tree_index[position]
                position -= position & -position
            updates.append((index, top_value + pairs[index][2], top_index))
        for index, value, parent in updates:
            best[index], previous[index] = value, parent
            position = pairs[index][1] + 1
            while position <= size:
                if value > tree_value[position]:
                    tree_value[position], tree_index[position] = value, index
                position += position & -position
    if not pairs:
        return {}
    cursor, anchors = max(range(len(pairs)), key=best.__getitem__), {}
    while cursor >= 0:
        anchors.setdefault(pairs[cursor][0], pairs[cursor][1])
        cursor = previous[cursor]
    return anchors


def run_offsets(timings, reference, anchors):
    """Group consecutive anchors whose offsets agree; a run of MIN_RUN anchors gives its median offset to every line it spans, and a gap
    between two runs that agree takes their offset too."""
    lines = sorted(anchors)
    offsets = [reference[anchors[line]][0] - timings[line][0] for line in lines]
    estimates, runs = [None] * len(timings), []
    start = 0
    while start < len(lines):
        end = start + 1
        while end < len(lines) and abs(offsets[end] - numpy.median(offsets[start:end])) <= AGREE_SECONDS and lines[end] - lines[end - 1] <= MAX_ANCHOR_GAP_LINES:
            end += 1
        if end - start >= MIN_RUN:
            median = float(numpy.median(offsets[start:end]))
            runs.append((end - start, median))
            for line in range(lines[start], lines[end - 1] + 1):
                estimates[line] = median
        start = end
    filled = list(estimates)
    known = [line for line, value in enumerate(estimates) if value is not None]
    for before, after in itertools.pairwise(known):
        if after - before > 1 and abs(estimates[before] - estimates[after]) <= AGREE_SECONDS:
            for line in range(before + 1, after):
                filled[line] = estimates[before]
    return filled, runs


def clean(text):
    text = re.sub(r"<[^>]+>|\{[^}]*\}", "", text or "")
    text = re.sub(r"^\s*-\s*", "", text, flags=re.M)
    return " ".join(text.split())
