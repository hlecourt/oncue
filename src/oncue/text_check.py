# SPDX-License-Identifier: GPL-3.0-or-later
"""The text check: which reference line each subtitle line translates (text_align), then two separate questions.

- Is this change safe? Each moved passage, and each lone line its own text puts elsewhere, is a candidate; a candidate is written only
  when its calibrated safety (text_confidence.passage_safety) reaches the write threshold and the result stays in order with the lines
  around it. Everything else stays a proposal and its minutes go to Review.
- Is the whole file verified? Only when every line is placed by agreeing text (or by a written passage), no contradiction or unreadable
  stretch remains, and the calibrated verification (text_confidence.file_verified) reaches the threshold. Only then is the file green.

Refine passes: after the first pass writes what is safe, the second looks only at what is left (candidates refused, lines left undecided
next to a written passage) and tries targeted alternatives: the core of a refused passage between its first and last anchored lines,
neighbouring passages on practically one offset merged, a passage extended over the undecided lines its move would otherwise jump, the
lines between a written passage and its neighbour settled again. A third pass runs only with new evidence (recognised speech from a
listener) and only on smaller regions (cores, seam lines). Every alternative gets features measured for its own move, goes through the
same calibrated safety, the same order rule and the caller's output check (a refused pass is undone whole); no pass moves a line an
earlier pass wrote or the text verified in place, nothing written counts as evidence for what follows, and the loop stops when a pass
writes nothing.

A passage's lines at a seam must carry evidence of their own at the place the move gives them (edge_evidence) or they are trimmed off the first
pass and left for the refine passes, which offer them again as separate calibrated alternatives, with the lines past the file's last anchor.

Off by default (ONCUE_TEXT_CHECK, ONCUE_TEXT_MODEL). Untranslated stretches (reference dialogue with no subtitle line) are reported with
their minutes, never filled."""

import itertools
import re

import numpy

from . import speech_check, text_align, text_confidence
from .outcome import FIX_KIND

IN_SYNC_SECONDS = 1.0
REPAIR_MARGIN = 2.0
MAX_TARGET_OVERLAP = 0.3
DOUBT_GAIN = 1.5
PLAUSIBLE_MOVE = 0.5
STAYS_SCORE = 4.0
STAYS_WORDS = 3
MAX_SEAM_WINDOWS = 120
MAX_PASSES = 3
MAX_EXTENSION_LINES = 12
MERGE_OFFSET = 0.25
EDGE_TEXT_SCORE = 2.0
EDGE_TEXT_GAIN = 1.0
EDGE_PAUSE = 60.0
MAX_EDGE_LINES = 40
MIN_EXTENDED_LINES = 3
EDGE_FAR_SECONDS = 300.0
MAX_JOINED_LINES = 3
MAX_LISTEN_LINES = 24
LISTEN_PAD = 0.3
LISTEN_NEUTRAL = 0.84
LISTEN_SCALE = 0.04
LISTEN_SAME_NEUTRAL = 0.87
LISTEN_SAME_SCALE = 0.05
MAX_LISTEN_SECONDS = 90.0
STRONG_CONTRADICTION = 2.0
MAX_UNSEEN_LINES = 4
MIN_REFERENCE_LINES = 20
ORDER_SLACK = 0.25
OVERLAP_SLACK = 0.25
LATE_END_SECONDS = 1.0
READING_TOLERANCE = 0.5
MIN_SHOWN_SECONDS = 0.3
END_DOUBT_SECONDS = 2.0
SHORT_SHARE = 0.3
SPEECH_FRAME = text_align.SPEECH_FRAME
VERDICT = {"confirmed": "confirmed", "corrected": "corrected", "unsettled": "unsettled"}
PLACED = ("anchored", "loose", "inside", "seam", "edge")
FINE_MAX_SECONDS = 2.5
FINE_MIN_MOVE = 0.65
FINE_AGREE = 0.25
FINE_GRID = 0.05
FINE_SWITCH = 3.0
FINE_CLIP = 1.0
FINE_MERGE = 0.25
FINE_MIN_ANCHORS = 6
FINE_MAX_MOVES = 2
MIN_LARGE_LINES = 3
MAX_LARGE_MOVES = 2
REFUSE_OFF_SPEECH = 1.0
DRIFT_MIN_QUARTERS = 3
FINE_LEVEL = 0.1
FINE_WHOLE_SHARE = 0.8
LEAD_MIN = 0.15
PROPOSAL_MIN_SAFETY = 0.1
CHANGED_SECONDS = 0.05
FINE_EDGE_ONSET = 0.25
FINE_EDGE_GAIN = 0.1
FINE_EDGE_RUN = 2
MARKUP = re.compile(r"<[^>]{1,500}>|\{[^}]{0,500}\}")


def decide(cues, reference_texts, embed, speech=None, speech_around=None, family="e5", listen=None, output_check=None, passes=MAX_PASSES,
           collect=False, speech_check_on=False, onsets=None):
    """The text's verdict on a subtitle (cues in file order). confirmed: the file is verified as it is; corrected: safe passages are
    written (timings), the file may still need Review; unsettled: nothing safe to write, the proposal (if any) is for a person. Without
    enough readable text on either side the verdict is unsettled: an enabled check that cannot read never lets timing alone write.
    listen: optional function [(start, end, line text)] -> [agreement in -1..1 or None], recognised speech at each place against the line
    (evidence for refine passes only, never a write on its own). output_check: optional function timings -> reason or "", the guard's own
    check of the file as it would be written; a pass whose result it refuses is undone. collect: every refine alternative is built from
    every first-pass candidate, for fitting the safety model. speech_check_on, onsets: the speech check is enabled and what it read
    (speech_check.onsets_of, None when it could not read): a check that is on and has no opinion never verifies a file."""
    reference = [(start, end, text) for start, end, text in ((start, end, clean(text)) for start, end, text in reference_texts or []) if text]
    lines = [clean(text) for _, _, text in cues]
    if len(reference) < MIN_REFERENCE_LINES or sum(bool(line) for line in lines) < MIN_REFERENCE_LINES:
        return unavailable("too little readable text", cues)
    order = sorted(range(len(cues)), key=lambda index: cues[index][0])
    ordered = [cues[index] for index in order]
    ordered_lines = [lines[index] for index in order]
    texts = [line or "-" for line in ordered_lines] + [text for _, _, text in reference]
    distinct = list(dict.fromkeys(texts))
    # each distinct text is embedded once: karaoke and sign tracks repeat the same line hundreds of times
    embedded = numpy.array(embed(distinct), dtype=numpy.float32)
    row = {text: index for index, text in enumerate(distinct)}
    vectors = embedded[[row[text] for text in texts]]
    line_vectors, reference_vectors = vectors[:len(ordered)], vectors[len(ordered):]
    line_vectors[[index for index, line in enumerate(ordered_lines) if unreadable(line)]] = 0.0
    found = text_align.align(ordered, reference, line_vectors, reference_vectors, speech, drift=speech_check_on and onsets is not None)
    found["misordered"] = misordered(order)
    if speech is None and speech_around is not None:
        # seam evidence: audio is read only around the lines between passages, then the alignment settles them again with it
        windows = seam_windows(ordered, found)
        speech = speech_around(windows) if windows else None
        if speech is not None:
            found = {**text_align.align(ordered, reference, line_vectors, reference_vectors, speech, drift=speech_check_on and onsets is not None), "misordered": found["misordered"]}
    ordered_check = None
    if output_check is not None:
        def ordered_check(timings):
            return output_check(in_file_order({"timings": timings, "proposal": None, "candidates": []}, order, cues)["timings"])
    settings = {"family": family, "listen": listen, "output_check": ordered_check, "passes": passes, "collect": collect, "speech_check_on": speech_check_on, "onsets": onsets}
    verdict = judge(ordered, reference, found, speech, settings)
    return in_file_order(verdict, order, cues)


def seam_windows(cues, found):
    """Where speech would settle something: each line between two passages (or past the last anchor of an edge passage), at its current
    place and at every place a neighbouring passage would take it."""
    windows, scale = [], found["scale"]
    passages = found["passages"]
    for left, right in itertools.pairwise(passages):
        if abs(left["offset"] - right["offset"]) <= text_align.SAME_OFFSET:
            continue
        for line in range(left["last"] + 1, right["first"]):
            start, end, _ = cues[line]
            windows.append((start, end))
            windows.extend((scale * start + passage["offset"], scale * end + passage["offset"]) for passage in (left, right))
    return windows[:MAX_SEAM_WINDOWS]


def unavailable(reason, cues=()):
    # no evidence at all: every minute stays doubtful, so the result is capped and listed for review
    return {"verdict": VERDICT["unsettled"], "timings": None, "proposal": None, "reason": reason, "off_minutes": [],
            "doubtful_minutes": every_minute(cues), "gaps": [], "coverage": 0.0, "contradictions": 0, "anchors": 0, "candidates": [],
            "safety": 0.0, "verified": 0.0, "longest_unseen": len(cues), "written": 0, "off_lines": 0}


def every_minute(cues):
    return sorted({int(start // 60) for start, _, _ in cues})


def unreadable(line):
    return not line or line.startswith(text_align.SOUND_TAG)


def judge(cues, reference, found, speech, settings):
    family = settings["family"]
    scale = found["scale"]
    targets = [target_offset(state, found["passages"], scale) for state in found["lines"]]
    starts = numpy.array([start for start, _, _ in reference])
    measured = [text_gain(found, starts, cues, line, offset, scale) for line, offset in enumerate(targets)]
    placeable = {line for line, offset in enumerate(targets) if offset is not None}
    for line, (state, offset) in enumerate(zip(found["lines"], targets, strict=True)):
        # a line the text places clearly where it is, on a reference line nothing else translates, is not dragged along (interleaved passages)
        gain, stays = measured[line]
        if offset is not None and state["how"] in ("inside", "seam", "edge") and gain <= -DOUBT_GAIN and stays:
            targets[line] = None
    found["gains"] = [gain for gain, _ in measured]
    found["heard"], found["written_offsets"], found["reference"], found["listened_seconds"] = {}, {}, reference, 0.0
    found["reference_starts"], found["reference_ends"], found["trimmed"] = starts, numpy.array([end for _, end, _ in reference]), set()
    found["own_groups"] = {line: group for group in found["groups"] for line in group["lines"]}
    onsets = settings["onsets"]
    found["reference_lead"] = reference_lead(reference, onsets)
    fine = fine_candidates(cues, reference, found, targets, onsets)
    for candidate in fine:
        for line in candidate["lines"]:
            targets[line] = None
    keep_in_order(cues, found, targets, scale)
    # lines the rules above kept where they are on purpose: no refine pass sweeps them along
    found["kept"] = {line for line in placeable if targets[line] is None} | found["misordered"]
    candidates = edge_trimmed(cues, found, candidate_passages(cues, found, targets, scale))
    for line in found["trimmed"]:
        targets[line] = None
    for candidate in candidates:
        candidate["origin"], candidate["added"] = "first", []
        led_by_reference(candidate, found)
        candidate["features"] = passage_features(candidate, cues, reference, found, speech)
    # a lone line cannot go back to a place another line already fills
    candidates = [candidate for candidate in candidates if candidate["kind"] == "passage" or candidate["features"]["target_overlap"] <= MAX_TARGET_OVERLAP]
    for candidate in candidates:
        candidate["safety"] = 0.0 if speech_refuses(candidate, cues, scale, onsets) else text_confidence.passage_safety(candidate["features"], family)
        candidate["pass"] = 1
    for candidate in fine:
        candidate["features"] = {**passage_features(candidate, cues, reference, found, speech), **candidate["fine"]}
        candidate["safety"] = text_confidence.fine_safety(candidate["features"], family)
        candidate["pass"] = 1
    candidates += fine
    written = select(cues, [], candidates, scale, settings["output_check"])
    passes = [{"pass": 1, "candidates": len(candidates), "written": len(written), "lines": sum(len(candidate["lines"]) for candidate in written)}]
    for number in range(2, settings["passes"] + 1):
        if number > 2 and settings["listen"] is None and not settings["collect"]:
            break
        blocked = [candidate for candidate in candidates if candidate["pass"] == 1 and candidate["safety"] >= text_confidence.WRITE_SAFETY
                   and all(candidate is not chosen for chosen in written)]
        found["blocked_offsets"] = {line: line_offset(candidate, line) for candidate in blocked for line in candidate["lines"]}
        fresh = refine_candidates(cues, reference, found, targets, candidates, written, speech, settings, number)
        candidates.extend(fresh)
        added = select(cues, written, fresh + blocked, scale, settings["output_check"])
        passes.append({"pass": number, "candidates": len(fresh), "written": len(added), "lines": sum(len(candidate["lines"]) for candidate in added)})
        written = written + added
        if not added and not settings["collect"]:
            break
    final = apply(cues, written, scale)
    for candidate in candidates:
        candidate["written"] = any(candidate is chosen for chosen in written)
    offered = [candidate for candidate in candidates if not candidate["written"] and candidate["pass"] == 1 and not candidate.get("refused_by_speech")]
    proposal = apply(cues, offered + written, scale) if offered or written else None
    hard, soft = doubtful(cues, found, targets, candidates, written, final)
    speech_hard, speech_opinion = speech_doubts(final, onsets)
    hard = sorted(set(hard) | speech_hard)
    soft = sorted(set(soft) - speech_hard)
    doubtful_lines = hard + soft
    file_features = verification_features(cues, found, hard, soft, candidates, final)
    verified = text_confidence.file_verified(file_features) if not hard and (speech_opinion or not settings["speech_check_on"]) else 0.0
    gaps = found["gaps"]
    doubtful_minutes = sorted({int(cues[line][0] // 60) for line in doubtful_lines} | gap_minutes(gaps))
    is_verified = verified >= text_confidence.VERIFY_SAFETY
    if written:
        verdict = VERDICT["corrected"]
    elif is_verified:
        verdict = VERDICT["confirmed"]
    else:
        verdict = VERDICT["unsettled"]
    return {"verdict": verdict, "timings": final if written else None, "proposal": proposal, "verified": round(verified, 3),
            "file_verified": is_verified, "safety": round(min((candidate["safety"] for candidate in written), default=0.0), 3),
            "file_safety": round(text_confidence.file_safety([candidate["safety"] for candidate in written]), 3),
            "written": len(written), "candidates": candidates, "doubtful_minutes": [] if is_verified and not gaps else doubtful_minutes,
            "off_minutes": sorted({int(cues[line][0] // 60) for candidate in candidates if not candidate["written"] for line in candidate["lines"]}),
            "off_lines": sum(len(candidate["lines"]) for candidate in candidates if not candidate["written"]),
            "gaps": [{"from": round(start, 2), "to": round(end, 2), "lines": count} for start, end, count in gaps],
            "coverage": round(file_features["anchored_share"], 3), "anchors": len(found["anchors"]), "scale": scale,
            "contradictions": file_features["contradictions"], "longest_unseen": file_features["longest_unseen"], "reason": "",
            "file_features": file_features, "passes": passes, "listened": len(found["heard"]),
            "vetting": {"onsets": onsets, "reference_lead": found["reference_lead"]}}


def select(cues, written, fresh, scale, output_check):
    """Which fresh candidates join the written ones: at the write threshold, the longest first among overlapping alternatives (each passed
    the same gate), never over a written line; a candidate that breaks the order around it is set aside (a fresh one next to a written one
    that it breaks too) and the alternatives it overlapped get their turn, until the file is in order. The caller's output check sees the
    result: a candidate it refuses on its own is set aside the same way; if it refuses only the combination, nothing of this pass is written."""
    set_aside = []
    while True:
        chosen = pick(written, [candidate for candidate in fresh if all(candidate is not other for other in set_aside)])
        if not chosen:
            return []
        final = apply(cues, written + chosen, scale)
        broken = [candidate for candidate in chosen if breaks_order(cues, final, candidate)]
        for old in (candidate for candidate in written if breaks_order(cues, final, candidate)):
            broken.append(min(chosen, key=lambda candidate: min(abs(candidate["lines"][0] - old["lines"][-1]), abs(old["lines"][0] - candidate["lines"][-1]))))
        if broken:
            set_aside.extend(broken)
            continue
        if output_check is None or not output_check(final):
            return chosen
        flawed = [candidate for candidate in chosen if output_check(apply(cues, [*written, candidate], scale))]
        if not flawed:
            return []
        set_aside.extend(flawed)


def pick(written, fresh):
    taken = {line for candidate in written for line in candidate["lines"]}
    chosen = []
    for candidate in sorted(fresh, key=lambda candidate: (-len(candidate["lines"]), -candidate["safety"])):
        if candidate["safety"] < text_confidence.WRITE_SAFETY or taken & set(candidate["lines"]):
            continue
        chosen.append(candidate)
        taken |= set(candidate["lines"])
    return chosen


def refine_candidates(cues, reference, found, targets, candidates, written, speech, settings, number):
    """Targeted alternatives for what is left after the passes before: the core of a refused passage, neighbouring passages on one offset
    merged, a passage extended over the undecided lines its move would jump, and the lines between a written passage and its neighbour settled again. None may touch a line
    already written or verified in place."""
    scale = found["scale"]
    final = apply(cues, written, scale)
    found["written_offsets"] = {line: line_offset(candidate, line) for candidate in written for line in candidate["lines"]}
    frozen = set(found["written_offsets"]) | verified_in_place(found, targets) | found["kept"]
    pool = [candidate for candidate in candidates if candidate["kind"] == "passage" and candidate["origin"] != "fine" and not candidate.get("refused_by_speech")
            and (settings["collect"] or all(candidate is not chosen for chosen in written))]
    alternatives = [core for core in (core_of(candidate, found) for candidate in pool) if core]
    structural = number == 2
    if structural:
        alternatives += merged_runs([candidate for candidate in pool if candidate["origin"] == "first"], targets, frozen)
    blocked = frozen - set(found["written_offsets"]) if settings["collect"] else frozen
    if structural:
        alternatives += [extension for extension in (extended(candidate, cues, targets, blocked, final, scale, found["written_offsets"])
                                                     for candidate in pool + alternatives) if extension]
    alternatives += joined_lines(cues, found, targets, written, frozen)
    if structural:
        alternatives += file_edge_lines(cues, found, targets, pool + list(written) + [alternative for alternative in alternatives if alternative["origin"] == "extended"], frozen)
    if settings["listen"] is not None:
        wanted = [(line, offset) for alternative in alternatives for line in alternative["added"] for offset in (alternative["offset"], 0.0)]
        listen_evidence(cues, found, wanted[:2 * MAX_LISTEN_LINES], settings["listen"])
    alternatives += seam_candidates(cues, found, targets, written, frozen, speech, settings)
    seen = {(tuple(candidate["lines"]), round(candidate["offset"], 3)) for candidate in candidates}
    fresh = []
    for alternative in alternatives:
        key = (tuple(alternative["lines"]), round(alternative["offset"], 3))
        if key in seen or (frozen if not settings["collect"] else blocked) & set(alternative["lines"]):
            continue
        seen.add(key)
        alternative["how"] = [found["lines"][line]["how"] for line in alternative["lines"]]
        alternative["features"] = passage_features(alternative, cues, reference, found, speech)
        alternative["safety"] = 0.0 if speech_refuses(alternative, cues, scale, settings["onsets"]) else text_confidence.passage_safety(alternative["features"], settings["family"])
        alternative["pass"] = number
        fresh.append(alternative)
    return fresh


def reference_lead(reference, onsets):
    """How far the reference track sits from the speech over the whole file (negative: it leads), when that is settled and worth correcting: a move onto a leading reference lands early by as much."""
    found = speech_check.file_measure([start for start, _, _ in reference], onsets)
    return found["offset"] if found and abs(found["offset"]) >= LEAD_MIN else 0.0


def led_by_reference(candidate, found):
    lead = found["reference_lead"]
    if not lead or candidate_is_drift(found["scale"]):
        return
    candidate["offset"] -= lead
    candidate["moves"] = {line: offset - lead for line, offset in candidate.get("moves", {}).items()}


def speech_refuses(candidate, cues, scale, onsets):
    """A move is refused, and not offered as a proposal either, when its lines would sit clearly off the speech, or when the speech has them closer to it now than the move would. A drift (scale) must also hold along the file."""
    if onsets is None:
        return False
    lines = candidate["lines"]
    moved = [scale * cues[line][0] + line_offset(candidate, line) for line in lines]
    after = speech_check.measure(moved, onsets)
    now = speech_check.measure([cues[line][0] for line in lines], onsets)
    settled_after, settled_now = speech_check.settled(after), speech_check.settled(now)
    refused = settled_after and abs(after["offset"]) > REFUSE_OFF_SPEECH
    refused = refused or (settled_now and abs(now["offset"]) <= speech_check.RESIDUAL_LIMIT and (not settled_after or abs(after["offset"]) > speech_check.RESIDUAL_LIMIT) and min(abs(place - cues[line][0]) for place, line in zip(moved, lines, strict=True)) > speech_check.RESIDUAL_LIMIT)
    if not refused and candidate_is_drift(scale):
        refused = not drift_holds(moved, onsets)
    candidate["refused_by_speech"] = refused
    return refused


def candidate_is_drift(scale):
    return scale != 1.0 and abs(scale - 1.0) <= text_align.DRIFT_MAX_SLOPE


def drift_holds(moved, onsets):
    # most quarters of the file measurable and none off the speech once corrected: the text's line through the anchors is only trusted that way
    size = len(moved) // 4
    quarters = [speech_check.measure(moved[first:first + size], onsets) for first in range(0, size * 4, size)] if size else []
    settled = [found for found in quarters if speech_check.settled(found, speech_check.FINE_PROMINENCE)]
    return len(settled) >= DRIFT_MIN_QUARTERS and all(abs(found["offset"]) <= speech_check.RESIDUAL_LIMIT for found in settled)


def speech_doubts(final, onsets):
    """The lines of rolling windows measurably off the speech in the final timing, and whether the check has an opinion at all (a settled measure of the whole file)."""
    if onsets is None:
        return set(), False
    order = sorted(range(len(final)), key=lambda line: final[line][0])
    starts = [final[line][0] for line in order]
    whole = speech_check.file_measure(starts, onsets)
    lines = {order[position] for positions, _ in speech_check.off_windows(starts, onsets) for position in positions}
    if whole and abs(whole["offset"]) > speech_check.RESIDUAL_LIMIT:
        lines = set(range(len(final)))
    return lines, whole is not None


def proposal_evidence(text, cues, timings, kind):
    """What the speech and the text say about a proposal (timings in file order) before a person sees it: the lines it changes, the speech under
    them now and once moved, and for a text proposal the best safety among the candidates it offers."""
    vetting = (text or {}).get("vetting")
    if not vetting or timings is None:
        return None
    changed = sorted((line for line, (after, before) in enumerate(zip(timings, cues, strict=True)) if abs(after[0] - before[0]) > CHANGED_SECONDS), key=lambda line: cues[line][0])
    onsets = vetting["onsets"]
    now = speech_check.measure([cues[line][0] for line in changed], onsets) if changed else None
    after = speech_check.measure(sorted(timings[line][0] for line in changed), onsets) if changed else None
    offered = [candidate["safety"] for candidate in text.get("candidates") or [] if not candidate["written"] and candidate.get("pass") == 1 and not candidate.get("refused_by_speech")]
    return {"kind": kind, "changed": len(changed), "best_safety": round(max(offered, default=0.0), 3) if kind == FIX_KIND["text"] else None,
            "reference_lead": vetting["reference_lead"],
            "speech_now": now and {"offset": round(now["offset"], 3), "prominence": round(now["prominence"], 2), "lines": now["lines"]},
            "speech_after": after and {"offset": round(after["offset"], 3), "prominence": round(after["prominence"], 2), "lines": after["lines"]}}


def proposal_doubt(evidence):
    """Why a proposal would make the file worse if a person accepted it, or "": the lines it changes would land clearly off the speech, or it is a
    text proposal whose every candidate the safety model finds implausible."""
    if not evidence or not evidence["changed"]:
        return ""
    if speech_check.settled(evidence["speech_after"]) and abs(evidence["speech_after"]["offset"]) > REFUSE_OFF_SPEECH:
        return "the lines it changes would sit off the speech"
    if evidence["kind"] == FIX_KIND["text"] and evidence["best_safety"] < PROPOSAL_MIN_SAFETY:
        return "no passage it moves is plausible"
    return ""


def led_timings(text, cues, timings):
    """A timing proposal puts lines on the reference's own timing; when the reference measurably leads or trails the speech over the file, the
    lines it changes are corrected by as much, as a text move is."""
    lead = ((text or {}).get("vetting") or {}).get("reference_lead") or 0.0
    if not lead:
        return timings
    return [(start - lead, end - lead, line) if abs(start - before[0]) > CHANGED_SECONDS else (start, end, line)
            for (start, end, line), before in zip(timings, cues, strict=True)]


def fine_candidates(cues, reference, found, targets, onsets):
    """Passages off by less than the text's passages can see: the levels of the one-to-one anchors' offsets, cut with a small Viterbi pass at a much finer grain, each corrected by how far the reference sits from the
    speech, and only when the speech measures the same move under the same lines. Text alone never makes one, nor speech alone; the edges of a move are trimmed to where the speech is under them, so a
    whole file off by under a second is moved too, its first and last lines without speech left in Review."""
    if onsets is None or found["scale"] != 1.0:
        return []
    anchors = [group for group in found["groups"] if group["anchor"] and len(group["lines"]) == 1 and len(group["references"]) == 1]
    if sum(abs(passage["offset"]) > FINE_MAX_SECONDS and passage["last"] - passage["first"] + 1 >= MIN_LARGE_LINES for passage in found["passages"]) >= MAX_LARGE_MOVES:
        return []
    near = [run for chunk in near_chunks(anchors) for run in fine_runs(chunk)]
    moves = [candidate for candidate in (fine_passage(cues, reference, found, targets, run, onsets) for run in near) if candidate]
    # several levels are a slope or a file too messy to cut this finely, not separate small moves: steps across a slope are what leaves a residual behind;
    # moves cut apart by stray anchors but on one level are one move
    if len(levels(moves)) > FINE_MAX_MOVES:
        return []
    # a whole file the passages already move (a second or more) keeps that move, edges included
    if sum(len(move["lines"]) for move in moves) > FINE_WHOLE_SHARE * len(cues) and sum(offset not in (None, 0.0) for offset in targets) > FINE_WHOLE_SHARE * len(cues):
        return []
    return moves


def levels(moves):
    offsets = sorted(move["offset"] for move in moves)
    groups = []
    for offset in offsets:
        if groups and offset - groups[-1][0] <= FINE_LEVEL:
            groups[-1].append(offset)
            continue
        groups.append([offset])
    return groups


def fine_passage(cues, reference, found, targets, run, onsets):
    agreeing = [anchor for anchor in run["anchors"] if abs(anchor["offset"] - run["level"]) <= FINE_AGREE]
    if len(agreeing) < FINE_MIN_ANCHORS:
        return None
    first, last = agreeing[0]["lines"][0], agreeing[-1]["lines"][0]
    target = run["level"] - found["reference_lead"]
    local = speech_check.measure([start for start, _, _ in reference if cues[first][0] + run["level"] <= start <= cues[last][0] + run["level"]], onsets)
    if speech_check.settled(local):
        target = run["level"] - local["offset"]
    moved = fine_move(cues, found, list(range(first, last + 1)), target, onsets)
    if not moved:
        return None
    # the unread lines at its edges that the move would run over go with it (added: they stay in Review), and nothing anchored elsewhere does
    anchored = {anchor["lines"][0] for anchor in found["groups"] if anchor["anchor"]} - set(moved["lines"])
    wider = extended(moved, cues, [None] * len(cues), anchored, list(cues), 1.0, {})
    if wider:
        moved = {**moved, "lines": wider["lines"], "added": wider["added"]}
    lines = speech_edges(cues, moved["lines"], moved["offset"], onsets)
    if not lines:
        return None
    # the gate judges the lines that will be written, measured again after extension and trimming; unsettled there, the move is only offered and its lines stay out of other moves
    final = fine_move(cues, found, lines, target, onsets)
    fine = final["fine"] if final else {**moved["fine"], "fine_prominence": 0.0, "fine_lines": float(len(lines)), "fine_share": len(lines) / len(cues)}
    kept = set(lines)
    return {**moved, "lines": lines, "added": [line for line in moved["added"] if line in kept], "how": [found["lines"][line]["how"] for line in lines],
            "trimmed": len(moved["lines"]) - len(lines), "fine": fine}


def speech_edges(cues, lines, offset, onsets):
    """The lines of a small move from its first to its last pair of consecutive lines whose moved start meets a speech onset, and clearly
    closer than where it starts now: under a second, the text cannot tell an edge line that belongs to the move from one in place whose
    translation simply starts late, so an edge without speech under it is left in Review. No such pair, no move."""
    expected = onsets + speech_check.BASELINE

    def evidenced(line):
        moved = onset_gap(expected, cues[line][0] + offset)
        return moved <= FINE_EDGE_ONSET and onset_gap(expected, cues[line][0]) >= moved + FINE_EDGE_GAIN

    flags = [evidenced(line) for line in lines]
    starts = [index for index in range(len(flags) - FINE_EDGE_RUN + 1) if all(flags[index:index + FINE_EDGE_RUN])]
    if not starts:
        return []
    return lines[starts[0]:starts[-1] + FINE_EDGE_RUN]


def onset_gap(expected, moment):
    index = int(numpy.searchsorted(expected, moment))
    near = [abs(float(expected[position]) - moment) for position in (index - 1, index) if 0 <= position < len(expected)]
    return min(near, default=numpy.inf)


def fine_move(cues, found, lines, target, onsets):
    subtitle = speech_check.measure([cues[line][0] for line in lines], onsets)
    if not speech_check.settled(subtitle, speech_check.FINE_PROMINENCE) or abs(target) < FINE_MIN_MOVE or abs(subtitle["offset"]) < FINE_MIN_MOVE or abs(subtitle["offset"] + target) > FINE_AGREE:
        return None
    return {"kind": "passage", "origin": "fine", "offset": (target - subtitle["offset"]) / 2, "lines": lines, "added": [], "how": [found["lines"][line]["how"] for line in lines],
            "fine": {"fine_prominence": subtitle["prominence"], "fine_agreement": abs(subtitle["offset"] + target), "fine_lines": float(len(lines)), "fine_share": len(lines) / len(cues)}}


def near_chunks(anchors):
    # consecutive anchors within reach of a small move; a passage far away is the other path's
    chunks, current = [], []
    for anchor in anchors:
        if abs(anchor["offset"]) > FINE_MAX_SECONDS:
            if current:
                chunks.append(current)
            current = []
            continue
        current.append(anchor)
    if current:
        chunks.append(current)
    return chunks


def fine_runs(anchors):
    """Runs of anchors on one level, by L1 distance clipped at FINE_CLIP over a grid of levels, changing level at FINE_SWITCH: {"anchors", "level"}."""
    if len(anchors) < FINE_MIN_ANCHORS:
        return []
    offsets = numpy.array([anchor["offset"] for anchor in anchors])
    levels = numpy.arange(-FINE_MAX_SECONDS, FINE_MAX_SECONDS + FINE_GRID / 2, FINE_GRID)
    emission = numpy.minimum(numpy.abs(offsets[:, None] - levels[None, :]), FINE_CLIP)
    cost = emission[0].copy()
    back = numpy.zeros(emission.shape, dtype=numpy.int32)
    for index in range(1, len(offsets)):
        best = int(cost.argmin())
        switch = cost[best] + FINE_SWITCH
        back[index] = numpy.where(cost <= switch, numpy.arange(len(levels)), best)
        cost = numpy.minimum(cost, switch) + emission[index]
    path = [int(cost.argmin())]
    for index in range(len(offsets) - 1, 0, -1):
        path.append(int(back[index, path[-1]]))
    path = path[::-1]
    runs, current = [], [0]
    for index in range(1, len(offsets)):
        if abs(levels[path[index]] - levels[path[current[0]]]) > FINE_MERGE:
            runs.append(current)
            current = []
        current.append(index)
    runs.append(current)
    return [{"anchors": [anchors[index] for index in run], "level": float(numpy.median(offsets[run]))} for run in runs]


def merged_runs(pool, targets, frozen):
    """Neighbouring passages on practically the same offset (a lone contradiction or a pause split one move in two) as one alternative,
    with the few unplaced lines between them; the offset is their line-weighted mean, never more than MERGE_OFFSET from either."""
    ordered = sorted(pool, key=lambda candidate: candidate["lines"][0])
    runs, current = [], []
    for candidate in ordered:
        if current and joinable(current[-1], candidate, current, targets, frozen):
            current.append(candidate)
            continue
        if len(current) > 1:
            runs.append(current)
        current = [candidate]
    if len(current) > 1:
        runs.append(current)
    merged = []
    for run in runs:
        lines = list(range(run[0]["lines"][0], run[-1]["lines"][-1] + 1))
        members = {line for candidate in run for line in candidate["lines"]}
        offset = sum(candidate["offset"] * len(candidate["lines"]) for candidate in run) / len(members)
        merged.append({"kind": "passage", "origin": "merged", "offset": offset, "lines": lines, "added": [line for line in lines if line not in members]})
    return merged


def joinable(left, right, run, targets, frozen):
    between = range(left["lines"][-1] + 1, right["lines"][0])
    offsets = [candidate["offset"] for candidate in run] + [right["offset"]]
    return (len(between) <= MAX_EXTENSION_LINES and max(offsets) - min(offsets) <= MERGE_OFFSET
            and all(line not in frozen and (targets[line] is None or abs(targets[line] - right["offset"]) <= text_align.SAME_OFFSET) for line in between))


def verified_in_place(found, targets):
    # lines whose own clear match puts them where they are: no pass may move them
    if found["scale"] != 1.0:
        return set()
    return {line for line, (state, offset) in enumerate(zip(found["lines"], targets, strict=True)) if state["how"] == "anchored" and offset == 0.0}


def core_of(candidate, found):
    """The passage between its first and last anchored lines: its edges (seam, edge, inside lines before the first anchor) left out."""
    anchored = [position for position, line in enumerate(candidate["lines"]) if found["lines"][line]["how"] == "anchored"]
    if len(anchored) < 2 or (anchored[0] == 0 and anchored[-1] == len(candidate["lines"]) - 1):
        return None
    return restricted(candidate, candidate["lines"][anchored[0]:anchored[-1] + 1], kind="passage", origin="core", added=[])


def extended(candidate, cues, targets, frozen, final, scale, written_offsets):
    """The passage with the lines its move would jump or cover: a passage moved later passes the unplaced lines after it, one moved earlier
    the lines before it. Only lines with no place of their own (or this one), never a written or verified line, and never a line next to
    a passage written on another offset (that line is a seam: joined_lines weighs it); None when the passage breaks no order or the lines
    it would jump are placed."""
    offset, lines = candidate["offset"], candidate["lines"]
    added_after, added_before = [], []
    previous = lines[-1]
    for line in range(lines[-1] + 1, min(len(cues), lines[-1] + 1 + MAX_EXTENSION_LINES + 1)):
        moved = (scale * cues[previous][0] + offset, scale * cues[previous][1] + offset)
        if not jumps(moved, final[line], cues[previous], cues[line]):
            break
        if line in frozen or (targets[line] is not None and abs(targets[line] - offset) > text_align.SAME_OFFSET) or len(added_after) == MAX_EXTENSION_LINES:
            return None
        added_after.append(line)
        previous = line
    following = lines[0]
    for line in range(lines[0] - 1, max(-1, lines[0] - 2 - MAX_EXTENSION_LINES), -1):
        moved = (scale * cues[following][0] + offset, scale * cues[following][1] + offset)
        if not jumps(final[line], moved, cues[line], cues[following]):
            break
        if line in frozen or (targets[line] is not None and abs(targets[line] - offset) > text_align.SAME_OFFSET) or len(added_before) == MAX_EXTENSION_LINES:
            return None
        added_before.append(line)
        following = line
    added = sorted(added_before + added_after)
    if not added:
        return None
    if any(abs(written_offsets[other] - offset) > text_align.SAME_OFFSET for line in added for other in (line - 1, line + 1) if other in written_offsets):
        return None
    if len(lines) < MIN_EXTENDED_LINES:
        return None
    return {"kind": "passage", "origin": "extended", "offset": offset, "lines": sorted(added + list(lines)), "added": added}


def joined_lines(cues, found, targets, written, frozen):
    """The undecided lines right next to a written passage, sent with it when each carries evidence of its own at that place against
    staying and against the offset of the next placed line beyond (at most MAX_JOINED_LINES, nearest first)."""
    if found["scale"] != 1.0:
        return []
    joined = []
    for candidate in written:
        for step, edge in ((1, candidate["lines"][-1]), (-1, candidate["lines"][0])):
            offset = line_offset(candidate, edge)
            beyond = next((found["written_offsets"][line] for line in range(edge + step, len(cues) if step > 0 else -1, step)
                           if line in found["written_offsets"]), 0.0)
            others = [other for other in dict.fromkeys((0.0, beyond)) if abs(other - offset) > text_align.SAME_OFFSET]
            lines = []
            for line in range(edge + step, edge + step * (MAX_JOINED_LINES + 1), step):
                if not 0 <= line < len(cues) or line in frozen or (targets[line] is not None and abs(targets[line] - offset) > text_align.SAME_OFFSET):
                    break
                if not edge_evidence(cues, found, line, offset, others):
                    break
                lines.append(line)
            if lines:
                joined.append({"kind": "passage", "origin": "seam", "offset": offset, "lines": sorted(lines), "added": sorted(lines)})
    return joined


def file_edge_lines(cues, found, targets, bases, frozen):
    """The lines past the last anchor of the file (or before the first), offered with the passage beside them: nothing the text reads says
    another offset applies there, so the time prior is that passage's own. All of them, and those up to the first long pause (credits often
    follow one); each alternative carries how far its lines are from the passage and what evidence they have, and the safety decides."""
    if not found["passages"] or not bases:
        return []
    alternatives = []
    tail = [base for base in bases if base["lines"][-1] >= found["passages"][-1]["first"]]
    head = [base for base in bases if base["lines"][0] <= found["passages"][0]["last"]]
    for base in tail:
        lines = range(base["lines"][-1] + 1, len(cues))
        alternatives += edge_runs(cues, targets, base, base["lines"][-1], lines, frozen)
    for base in head:
        lines = range(base["lines"][0] - 1, -1, -1)
        alternatives += edge_runs(cues, targets, base, base["lines"][0], lines, frozen)
    return alternatives


def edge_runs(cues, targets, base, edge, lines, frozen):
    offset = line_offset(base, edge)
    run, previous, close = [], edge, None
    for line in lines:
        if line in frozen or (targets[line] is not None and abs(targets[line] - offset) > text_align.SAME_OFFSET):
            return []
        if close is None and abs(cues[line][0] - cues[previous][0]) > EDGE_PAUSE:
            close = list(run)
        run.append(line)
        previous = line
    if not run or len(run) > MAX_EDGE_LINES:
        return []
    return [{"kind": "passage", "origin": "edge", "offset": offset, "lines": sorted(part), "added": sorted(part), "base_lines": len(base["lines"])}
            for part in (run, close) if part]


def jumps(earlier, later, earlier_then, later_then):
    # the later line would start before the earlier one, or inside it, where the original did not
    inverted = later[0] < earlier[0] - ORDER_SLACK and not later_then[0] < earlier_then[0] - ORDER_SLACK
    covered = earlier[0] <= later[0] < earlier[1] - OVERLAP_SLACK and not earlier_then[0] <= later_then[0] < earlier_then[1] - OVERLAP_SLACK
    return inverted or covered


def seam_candidates(cues, found, targets, written, frozen, speech, settings):
    """Lines between a written passage and the next placed line (written, verified in place, or the file's edge, where the other choice
    is to stay) settled again with one cut, on the same evidence as the first pass plus recognised speech when a listener is given. The
    lines the cut sends with a moved side are a candidate; nothing when no cut wins by text_align.SEAM_MARGIN."""
    scale = found["scale"]
    if scale != 1.0 or not written:
        return []
    offsets = {**found.get("blocked_offsets", {}), **found["written_offsets"]}
    offsets.update({line: 0.0 for line in frozen if line not in offsets})
    placed = sorted(offsets)
    runs = []
    for left, right in itertools.pairwise([-1, *placed, len(cues)]):
        between = list(range(left + 1, right))
        left_offset = offsets.get(left, 0.0)
        right_offset = offsets.get(right, 0.0)
        if not between or len(between) > MAX_EXTENSION_LINES or abs(left_offset - right_offset) <= text_align.SAME_OFFSET:
            continue
        if left not in found["written_offsets"] and right not in found["written_offsets"] and left not in offsets and right not in offsets:
            continue
        runs.append((between, left_offset, right_offset))
    if settings["listen"] is not None:
        wanted = [(line, offset) for between, left_offset, right_offset in runs for line in between for offset in (left_offset, right_offset)]
        listen_evidence(cues, found, wanted[:2 * MAX_LISTEN_LINES], settings["listen"])
    starts = numpy.array([start for start, _, _ in found["reference"]])
    by_line = {line: group for group in found["groups"] for line in group["lines"]}
    scores = {"one": found["scores"], "readable": found["readable"]}
    fresh = []
    for between, left_offset, right_offset in runs:
        evidence = [(text_align.line_evidence(cues, line, left_offset, by_line, starts, scale, scores, (speech, found["heard"])),
                     text_align.line_evidence(cues, line, right_offset, by_line, starts, scale, scores, (speech, found["heard"]))) for line in between]
        totals = [sum(left for left, _ in evidence[:cut]) + sum(right for _, right in evidence[cut:]) for cut in range(len(between) + 1)]
        best = int(numpy.argmax(totals))
        rival = max((total for cut, total in enumerate(totals) if cut != best), default=-numpy.inf)
        if totals[best] - rival < text_align.SEAM_MARGIN:
            continue
        for lines, offset in ((between[:best], left_offset), (between[best:], right_offset)):
            if lines and offset != 0.0 and all(seam_line_supported(cues, found, line, offset) for line in lines):
                fresh.append({"kind": "passage", "origin": "seam", "offset": offset, "lines": lines, "added": lines,
                              "cut_margin": float(totals[best] - rival)})
    return fresh


def listen_evidence(cues, found, wanted, listen):
    """Recognised speech at each (line, offset) place not heard yet, against the line's text and, when the text matched the line to a
    reference line, against that reference line (same language as the speech): stored for line_evidence and the features. At most
    MAX_LISTEN_SECONDS of audio per file."""
    scale = found["scale"]
    by_line = {line: group for group in found["groups"] for line in group["lines"]}
    requests, keys = [], []
    for line, offset in wanted:
        start, end = scale * cues[line][0] + offset, scale * cues[line][1] + offset
        key = (line, round(start, 1))
        if key in found["heard"] or key in keys or start < 0 or unreadable(clean(cues[line][2])):
            continue
        if found["listened_seconds"] + end - start + 2 * LISTEN_PAD > MAX_LISTEN_SECONDS:
            break
        found["listened_seconds"] += end - start + 2 * LISTEN_PAD
        group = by_line.get(line)
        reference_text = " ".join(found["reference"][index][2] for index in group["references"]) if group else None
        requests.append((start - LISTEN_PAD, end + LISTEN_PAD, cues[line][2], reference_text))
        keys.append(key)
    if not requests:
        return
    for key, agreement in zip(keys, listen(requests), strict=True):
        if agreement is not None:
            found["heard"][key] = float(agreement)


def listen_agreements(requests, words, windows, embed):
    """Recognised words against each line: for every (start, end, line[, reference line]) request whose span lies in a transcribed window,
    the words said in it (by their middle) joined and embedded with the line (across languages) and with the reference line the text
    matched it to (same language), each cosine mapped around its own neutral point into -1..1, the stronger kept; None when the span was
    not read or nothing was recognised there (a missed phrase proves nothing)."""
    middles = numpy.array([(start + end) / 2 for start, end, _ in words]) if words else numpy.zeros(0)
    said = []
    for request in requests:
        start, end = request[0], request[1]
        if not any(window_start <= start + LISTEN_PAD and end - LISTEN_PAD <= window_end for window_start, window_end in windows):
            said.append("")
            continue
        low, high = numpy.searchsorted(middles, [start, end])
        said.append(" ".join(words[index][2].strip() for index in range(low, high)).strip())
    heard = [index for index, text in enumerate(said) if text]
    agreements = [None] * len(requests)
    if not heard:
        return agreements
    references = [index for index in heard if len(requests[index]) > 3 and requests[index][3]]
    texts = [said[index] for index in heard] + [clean(requests[index][2]) or "-" for index in heard] + [clean(requests[index][3]) or "-" for index in references]
    vectors = numpy.array(embed(texts), dtype=numpy.float32)
    same = {index: float(vectors[heard.index(index)] @ vectors[2 * len(heard) + position]) for position, index in enumerate(references)}
    for position, index in enumerate(heard):
        across = (float(vectors[position] @ vectors[len(heard) + position]) - LISTEN_NEUTRAL) / LISTEN_SCALE
        within = (same[index] - LISTEN_SAME_NEUTRAL) / LISTEN_SAME_SCALE if index in same else -1.0
        agreements[index] = min(1.0, max(-1.0, across, within))
    return agreements


def keep_in_order(cues, found, targets, scale):
    """An unread line goes with its passage only if its moved place keeps it between its moved neighbours, and only if the file itself has
    it in time order; otherwise it is most often a line already in place among moved ones, and it stays where it is."""
    for line, (state, offset) in enumerate(zip(found["lines"], targets, strict=True)):
        if offset is None or state["how"] not in ("inside", "seam", "edge") or (offset == 0.0 and scale == 1.0):
            continue
        if line in found["misordered"]:
            targets[line] = None
            continue
        moved = scale * cues[line][0] + offset
        before = next((placed(cues, other, targets, scale) for other in range(line - 1, -1, -1) if targets[other] is not None), None)
        after = next((placed(cues, other, targets, scale) for other in range(line + 1, len(cues)) if targets[other] is not None), None)
        if (before is not None and moved < before - ORDER_SLACK) or (after is not None and moved > after + ORDER_SLACK):
            targets[line] = None


def misordered(order):
    # lines whose place in the file disagrees with their place in time (sorted position -> file index not increasing around them)
    return {position for position in range(len(order))
            if (position > 0 and order[position - 1] > order[position]) or (position + 1 < len(order) and order[position] > order[position + 1])}


def placed(cues, line, targets, scale):
    offset = targets[line]
    return cues[line][0] if offset == 0.0 and scale == 1.0 else scale * cues[line][0] + offset


def text_gain(found, starts, cues, line, offset, scale):
    """How much better the line's text fits where its passage would take it than where it is, and whether where it is holds a clear match
    to a reference line nothing else translates (the mark of a line left in place among moved ones)."""
    if offset is None:
        return 0.0, False
    start, end, _ = cues[line]
    now, now_reference = text_align.local_score(found["scores"], starts, line, start, end)
    moved, _ = text_align.local_score(found["scores"], starts, line, scale * start + offset, scale * end + offset)
    stays = (now >= STAYS_SCORE and len(cues[line][2].split()) >= STAYS_WORDS and now_reference is not None
             and now_reference not in found["matched_references"])
    return max(moved, 0.0) - max(now, 0.0), stays


def target_offset(state, passages, scale):
    """The offset a line would take, or None: its passage's offset, zero when that is within IN_SYNC_SECONDS (a line is never nudged by
    less than the tolerance), its own offset for a lone line whose text clearly puts it elsewhere inside an in-sync passage. A contradicted
    line inside a moved passage moves with it: leaving it behind would break the order, and its contradiction counts against the passage."""
    if state["offset"] is None:
        return None
    offset = state["offset"] if scale != 1.0 or abs(state["offset"]) >= IN_SYNC_SECONDS else 0.0
    if state["how"] != "contradiction" or offset != 0.0:
        return offset
    own = state["own_offset"]
    if offset == 0.0 and abs(own) >= IN_SYNC_SECONDS and state["margin"] >= REPAIR_MARGIN:
        return own
    return None


def candidate_passages(cues, found, targets, scale):
    """Runs of consecutive lines taking practically one non-zero offset (neighbouring passages within MERGE_OFFSET of each other are one
    move, each line keeping its own passage's offset); a lone contradiction repair is its own candidate."""
    candidates, current = [], None
    for line, offset in enumerate(targets):
        moves = offset is not None and (offset != 0.0 or scale != 1.0)
        state = found["lines"][line]
        single = moves and state["how"] == "contradiction" and scale == 1.0 and abs(state["offset"]) < IN_SYNC_SECONDS
        if current and moves and not single and current["kind"] == "passage" and joins(current, offset):
            current["lines"].append(line)
            current["moves"][line] = offset
            continue
        current = None
        if moves:
            current = {"kind": "single" if single else "passage", "offset": offset, "lines": [line], "moves": {line: offset}}
            candidates.append(current)
    for candidate in candidates:
        candidate["offset"] = float(numpy.median(list(candidate["moves"].values())))
        candidate["how"] = [found["lines"][line]["how"] for line in candidate["lines"]]
    return candidates


def joins(current, offset):
    offsets = list(current["moves"].values())
    return max(*offsets, offset) - min(*offsets, offset) <= MERGE_OFFSET


def line_offset(candidate, line):
    return candidate.get("moves", {}).get(line, candidate["offset"])


def restricted(candidate, lines, **changes):
    """The candidate over some of its lines, each keeping its own offset."""
    moves = {line: line_offset(candidate, line) for line in lines}
    return {**candidate, "lines": list(lines), "moves": moves, "offset": float(numpy.median(list(moves.values()))), **changes}


def edge_trimmed(cues, found, candidates):
    """A passage's lines at a seam (next to lines another passage places elsewhere) must carry evidence of their own at the place the move
    gives them; the ones that do not are trimmed off and left for a refine pass or for Review. A file edge is left as the first pass placed
    it (a whole-file move takes its edges along, marked unsupported)."""
    trimmed = []
    for candidate in candidates:
        if candidate["kind"] != "passage":
            trimmed.append(candidate)
            continue
        lines = candidate["lines"]
        left = seam_alternatives(found, lines[0], -1, candidate["offset"])
        right = seam_alternatives(found, lines[-1], 1, candidate["offset"])
        first, last = 0, len(lines) - 1
        while left and first <= last and not edge_evidence(cues, found, lines[first], line_offset(candidate, lines[first]), left):
            first += 1
        while right and last >= first and not edge_evidence(cues, found, lines[last], line_offset(candidate, lines[last]), right):
            last -= 1
        found["trimmed"].update(lines[:first] + lines[last + 1:])
        if first > last:
            continue
        if first == 0 and last == len(lines) - 1:
            trimmed.append(candidate)
            continue
        trimmed.append(restricted(candidate, lines[first:last + 1], trimmed=len(lines) - (last - first + 1)))
    return trimmed


def seam_alternatives(found, edge, step, offset):
    """The places a candidate's edge line could otherwise take: where it is, and the offset of the nearest passage beyond the edge. Empty
    when no passage lies beyond (a file edge)."""
    passages = [passage for passage in found["passages"] if (passage["first"] > edge if step > 0 else passage["last"] < edge)]
    if not passages:
        return []
    beyond = passages[0] if step > 0 else passages[-1]
    alternatives = [0.0] if found["scale"] == 1.0 else []
    alternatives.append(beyond["offset"])
    return [other for other in dict.fromkeys(alternatives) if abs(other - offset) > text_align.SAME_OFFSET]


def edge_evidence(cues, found, line, offset, alternatives):
    """Evidence of the line's own for `offset`: its text reads clearly at that place (its own agreeing anchor, or a strong match there) and
    clearly better than at every other place it could take, so a phrase said at both places is no evidence; or, for a line the text cannot
    tell, its start and end meet a reference line's start and end there and nowhere else it could go. A line whose own match, however weak,
    puts it elsewhere has none. The other places always include the passages on either side, written or not."""
    scale, scores = found["scale"], found["scores"]
    starts, ends = found["reference_starts"], found["reference_ends"]
    start, end, _ = cues[line]
    alternatives = [other for other in dict.fromkeys([*alternatives, *neighbouring_offsets(found, line)]) if abs(other - offset) > text_align.SAME_OFFSET]
    if own_match_elsewhere(found, line, offset):
        return False
    if found["readable"][line]:
        here, index = text_align.local_score(scores, starts, line, scale * start + offset, scale * end + offset)
        anchored = found["lines"][line]["how"] == "anchored"
        if anchored or (here >= EDGE_TEXT_SCORE and meets(starts, scale * start + offset)):
            rivals = [rival_score(scores, starts, line, scale * start + other, scale * end + other, index) if other else
                      rival_score(scores, starts, line, start, end, index) for other in alternatives]
            if all(here - rival >= EDGE_TEXT_GAIN for rival in rivals):
                return True
        if here < EDGE_TEXT_SCORE:
            return False
    return (meets(starts, scale * start + offset) and meets(ends, scale * end + offset)
            and not any(meets(starts, (scale * start + other) if other else start) for other in alternatives))


def seam_line_supported(cues, found, line, offset):
    # a line the cut would send along: its own match, however weak, must agree, or it must carry evidence of its own
    if own_match_elsewhere(found, line, offset):
        return False
    return line in found["own_groups"] or edge_evidence(cues, found, line, offset, [0.0])


def own_match_elsewhere(found, line, offset):
    own = found["own_groups"].get(line)
    return own is not None and abs(own["offset"] - offset) > text_align.SAME_OFFSET


def neighbouring_offsets(found, line):
    before = [passage for passage in found["passages"] if passage["last"] < line]
    after = [passage for passage in found["passages"] if passage["first"] > line]
    return [passage["offset"] for passage in (before[-1:] + after[:1])]


def rival_score(scores, reference_starts, line, start, end, exclude):
    """The line's best normalised score against reference lines starting around a place, the reference line it reads at its own place left
    out (a move of a second or two leaves the same reference line within reach of both places)."""
    low, high = numpy.searchsorted(reference_starts, [start - text_align.LOCAL_WINDOW, end + text_align.LOCAL_WINDOW])
    if high <= low:
        return 0.0
    row = scores[line, low:high].copy()
    if exclude is not None and low <= exclude < high:
        row[exclude - low] = -numpy.inf
    return float(max(row.max(), 0.0)) if row.size and numpy.isfinite(row.max()) else 0.0


def meets(moments, moment):
    return bool(moments.size) and float(numpy.min(numpy.abs(moments - moment))) <= text_align.COINCIDENCE


def passage_features(candidate, cues, reference, found, speech):
    lines, offset, scale = candidate["lines"], candidate["offset"], found["scale"]
    states = [found["lines"][line] for line in lines]
    by_line = {line: group for group in found["groups"] for line in group["lines"]}
    margins = [state.get("margin", 0.0) for state in states if state["how"] == "anchored"]
    own = [(by_line[line], line_offset(candidate, line)) for line in lines if line in by_line]
    starts = numpy.array([start for start, _, _ in reference])
    moved = [scale * cues[line][0] + line_offset(candidate, line) for line in lines]
    first, last = lines[0], lines[-1]
    neighbours = [found["lines"][line]["how"] for line in (first - 1, last + 1) if 0 <= line < len(cues)]
    longest_inside = longest_run([state["how"] == "inside" for state in states])
    speech_now = speech_moved = 0.5
    if speech is not None:
        speech_now = float(numpy.mean([text_align.speech_share(speech, cues[line][0], cues[line][1]) for line in lines]))
        speech_moved = float(numpy.mean([text_align.speech_share(speech, scale * cues[line][0] + line_offset(candidate, line),
                                                                 scale * cues[line][1] + line_offset(candidate, line)) for line in lines]))
    seam_margins = [state.get("seam_margin", 0.0) for state in states if state["how"] == "seam"]
    # measured for this candidate's own offset: a merged or extended alternative moves lines by an offset their first-pass gain never saw
    gains = [text_gain(found, starts, cues, line, line_offset(candidate, line), scale)[0] for line in lines]
    occupied = [cue for index, cue in enumerate(cues) if index not in set(lines) and abs(cue[0] - moved[0]) < 30]
    target_overlap = max((overlap(cue, (moved[0], scale * cues[line][1] + offset)) for cue in occupied for line in lines[:1]), default=0.0)
    return {
        "single": float(candidate["kind"] == "single"), "lines": float(len(lines)), "log_lines": float(numpy.log1p(len(lines))),
        "anchored_share": len(margins) / len(lines), "inside_share": sum(state["how"] == "inside" for state in states) / len(lines),
        "seam_lines": float(sum(state["how"] in ("seam", "edge") for state in states)),
        "min_seam_margin": float(min(seam_margins, default=3.0)),
        "contradictions": float(sum(state["how"] == "contradiction" for state in states)) if candidate["kind"] == "passage" else 0.0,
        "max_contradiction": float(max((state["margin"] for state in states if state["how"] == "contradiction"), default=0.0))
        if candidate["kind"] == "passage" else 0.0,
        "min_margin": float(min(margins, default=states[0].get("margin", 0.0))),
        "median_margin": float(numpy.median(margins)) if margins else float(states[0].get("margin", 0.0)),
        "own_agree_share": sum(abs(group["offset"] - wanted) <= text_align.SAME_OFFSET for group, wanted in own) / max(len(own), 1),
        "offset": float(abs(offset)), "scaled": float(scale != 1.0), "file_share": len(lines) / len(cues),
        "coincide_moved": coincidence_share(starts, moved), "coincide_now": coincidence_share(starts, [cues[line][0] for line in lines]),
        "longest_inside": float(longest_inside), "undecided_neighbours": float(sum(how == "undecided" for how in neighbours)),
        "speech_gain": speech_moved - speech_now, "speech_known": float(speech is not None),
        "short": float(numpy.mean([len(cues[line][2].split()) <= 2 for line in lines])), "target_overlap": float(target_overlap),
        "text_gain": float(numpy.mean(gains)), "worst_gain": float(min(gains)),
        "file_anchored": len(found["anchors"]) / len(cues), "file_passages": float(numpy.log1p(len(found["passages"]))),
        "unsupported_edges": float(sum(bool(state.get("unsupported")) for state in states)),
        "source_overlaps": float(sum(cues[line][0] < cues[line - 1][1] - OVERLAP_SLACK for line in range(max(first - 3, 1), min(last + 4, len(cues))))),
        **refine_features(candidate, cues, found, speech),
        **boundary_features(candidate, cues, found),
    }


def refine_features(candidate, cues, found, speech):
    """What a refine pass changed: where the alternative comes from, the lines it adds without their own evidence and how well they read
    at its place against staying, and recognised speech. Nothing an earlier pass wrote counts as evidence."""
    lines, offset, scale = candidate["lines"], candidate["offset"], found["scale"]
    origin = candidate.get("origin", "first")
    added = candidate.get("added") or []
    starts = numpy.array([start for start, _, _ in found["reference"]])
    by_line = {line: group for group in found["groups"] for line in group["lines"]}
    scores = {"one": found["scores"], "readable": found["readable"]}
    audio = (speech, found["heard"])
    support = [text_align.line_evidence(cues, line, offset, by_line, starts, scale, scores, audio)
               - text_align.line_evidence(cues, line, 0.0, by_line, starts, 1.0, scores, audio) for line in added]
    heard = [found["heard"][(line, round(scale * cues[line][0] + offset, 1))] for line in lines if (line, round(scale * cues[line][0] + offset, 1)) in found["heard"]]
    deviations = [abs(found["lines"][line]["own_offset"] - offset) for line in lines if found["lines"][line]["how"] in ("anchored", "loose")]
    return {"offset_spread": float(numpy.median(deviations)) if deviations else 0.0, "worst_deviation": float(max(deviations, default=0.0)),
            "origin_core": float(origin == "core"), "origin_merged": float(origin == "merged"), "origin_extended": float(origin == "extended"), "origin_seam": float(origin == "seam"),
            "added_lines": float(len(added)), "added_support": float(min(support, default=0.0)),
            "heard_share": len(heard) / len(lines), "heard_agreement": float(numpy.mean(heard)) if heard else 0.0,
            "cut_margin": float(candidate.get("cut_margin", 0.0))}


def boundary_features(candidate, cues, found):
    """The risks at a move's edges: edge lines without evidence of their own, how far its lines get from an anchored line, unread lines
    sitting on another line in the file (two streams interleaved: nothing says which one they follow), and the move's first or last line
    overlapping a line outside it, the mark of a passage shifted onto its neighbours."""
    lines, scale = candidate["lines"], found["scale"]
    members = set(lines)
    states = found["lines"]
    interleaved = sum(states[line]["how"] in ("inside", "seam", "edge", "undecided") and overlaps_neighbour(cues, line) for line in lines)
    first, last = lines[0], lines[-1]
    crossing = (first > 0 and first - 1 not in members and cues[first][0] < cues[first - 1][1] - OVERLAP_SLACK) + (
        last + 1 < len(cues) and last + 1 not in members and cues[last + 1][0] < cues[last][1] - OVERLAP_SLACK)
    edges = [first, last] if first != last else [first]
    unevidenced = sum(not edge_evidence(cues, found, line, line_offset(candidate, line), [0.0] if scale == 1.0 else []) for line in edges)
    anchored = [line for line in range(max(first - MAX_EDGE_LINES, 0), min(last + MAX_EDGE_LINES, len(cues) - 1) + 1) if states[line]["how"] == "anchored"
                and abs(states[line]["offset"] - candidate["offset"]) <= text_align.SAME_OFFSET]
    moments = numpy.array([cues[line][0] for line in anchored]) if anchored else None
    distance = max((float(numpy.min(numpy.abs(moments - cues[line][0]))) for line in edges), default=0.0) if moments is not None else EDGE_FAR_SECONDS
    added = candidate.get("added") or []
    moved_starts = [(scale * cues[line][0] + line_offset(candidate, line), scale * cues[line][1] + line_offset(candidate, line)) for line in added]
    contradicted = sum(found["own_groups"][line]["anchor"] and abs(found["own_groups"][line]["offset"] - line_offset(candidate, line)) > text_align.SAME_OFFSET
                       for line in added if line in found["own_groups"])
    return {"interleaved": float(interleaved), "crossing": float(crossing), "edges_unevidenced": float(unevidenced), "edge_seconds": float(min(distance, EDGE_FAR_SECONDS)),
            "origin_edge": float(candidate.get("origin") == "edge"), "base_log_lines": float(numpy.log1p(candidate.get("base_lines", 0))),
            "added_evidenced": float(numpy.mean([edge_evidence(cues, found, line, line_offset(candidate, line), [0.0]) for line in added])) if added else 0.0,
            "added_meet": float(numpy.mean([meets(found["reference_starts"], start) for start, _ in moved_starts])) if added else 0.0,
            "added_meet_both": float(numpy.mean([meets(found["reference_starts"], start) and meets(found["reference_ends"], end) for start, end in moved_starts])) if added else 0.0,
            "added_unread": float(numpy.mean([not found["readable"][line] for line in added])) if added else 0.0,
            "added_contradicted": float(contradicted)}


def overlaps_neighbour(cues, line):
    return (line > 0 and cues[line][0] < cues[line - 1][1] - OVERLAP_SLACK) or (line + 1 < len(cues) and cues[line + 1][0] < cues[line][1] - OVERLAP_SLACK)


def overlap(cue, span):
    # share of the span another line already covers
    return max(0.0, min(cue[1], span[1]) - max(cue[0], span[0])) / max(span[1] - span[0], 1e-6)


def coincidence_share(starts, moments):
    if not starts.size or not moments:
        return 0.0
    return float(numpy.mean([numpy.min(numpy.abs(starts - moment)) <= text_align.COINCIDENCE for moment in moments]))


def longest_run(flags):
    longest = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def apply(cues, candidates, scale):
    final = list(cues)
    for candidate in candidates:
        for line in candidate["lines"]:
            start, end, text = cues[line]
            offset = line_offset(candidate, line)
            final[line] = (scale * start + offset, scale * end + offset, text)
    return final


def breaks_order(cues, final, candidate):
    """A written candidate must not start before the line before it, nor end after the line after it starts, by more than the original
    already did: the seams of a passage are part of the change."""
    first, last = candidate["lines"][0], candidate["lines"][-1]
    if final[first][0] < 0:
        return True
    if first > 0 and jumps(final[first - 1], final[first], cues[first - 1], cues[first]):
        return True
    return last + 1 < len(cues) and jumps(final[last], final[last + 1], cues[last], cues[last + 1])


def doubtful(cues, found, targets, candidates, written, final):
    """Lines a person should look at. Hard: in an unwritten candidate the model finds plausible, clearly contradicted by their own text, or
    ending well into the next line; the file is never verified with one. Soft: undecided, weakly contradicted, in an implausible
    candidate, inside an unread stretch longer than MAX_UNSEEN_LINES, on a passage off by more than the reading tolerance but less than a
    move, ending far from the reference line it translates or shown for a blink, unreadable without a reference start under it, or
    written without evidence of its own (added to a passage, or an unsupported edge). Nothing written counts as proof of itself."""
    written_lines = {line for candidate in written for line in candidate["lines"]}
    plausible = {line for candidate in candidates if not candidate["written"] and candidate["safety"] >= PLAUSIBLE_MOVE
                 for line in candidate["lines"]} - written_lines
    hard, soft = set(plausible), set()
    for line, (state, offset) in enumerate(zip(found["lines"], targets, strict=True)):
        if line in written_lines or line in plausible:
            continue
        if state["how"] == "contradiction" and state["margin"] >= STRONG_CONTRADICTION:
            hard.add(line)
        elif offset is None or state["how"] not in PLACED or (offset != 0.0 or found["scale"] != 1.0) or (state["offset"] is not None and abs(state["offset"]) >= READING_TOLERANCE):
            soft.add(line)
    soft.update(line for candidate in written for line in candidate.get("added") or [])
    soft.update(line for line in written_lines if found["lines"][line].get("unsupported"))
    soft.update(badly_ended(final, found))
    soft.update(unread_without_start(cues, final, found))
    hard.update(line for line in range(len(final) - 1) if final[line][1] > final[line + 1][0] + LATE_END_SECONDS)
    unseen = []
    for line in range(len(cues) + 1):
        if line < len(cues) and found["lines"][line]["how"] in ("inside", "edge", "seam", "loose") and line not in written_lines:
            unseen.append(line)
            continue
        if len(unseen) > MAX_UNSEEN_LINES:
            soft.update(unseen)
        unseen = []
    return sorted(hard), sorted(soft - hard)


def badly_ended(final, found):
    """Lines shown for a blink, and lines matched one to one whose end lands far from the reference line's end or that last a small part
    of it: the text places starts, so ends need their own look."""
    reference = found["reference"]
    ends = numpy.array([end for _, end, _ in reference])
    lines = {line for line, (start, end, _) in enumerate(final) if end - start < MIN_SHOWN_SECONDS}
    for group in found["groups"]:
        if len(group["lines"]) != 1 or len(group["references"]) != 1 or not group["anchor"]:
            continue
        line, (reference_start, reference_end, _) = group["lines"][0], reference[group["references"][0]]
        start, end, _ = final[line]
        # in reference time the line ends at end - (start - reference_start); a translation may end with any reference line, not just its own
        if numpy.min(numpy.abs(ends - (end - start + reference_start))) > END_DOUBT_SECONDS or (reference_end - reference_start >= 1.0 and end - start < SHORT_SHARE * (reference_end - reference_start)):
            lines.add(line)
    return lines


def unread_without_start(cues, final, found):
    # a line the text cannot read (a sound, an empty line) is vouched for only by a reference line starting under it
    starts = numpy.array([start for start, _, _ in found["reference"]])
    return {line for line, (start, _, text) in enumerate(final)
            if unreadable(clean(text)) and (not starts.size or numpy.min(numpy.abs(starts - start)) > text_align.COINCIDENCE)}


def verification_features(cues, found, hard, soft, candidates, final):
    states = found["lines"]
    anchored = sum(state["how"] == "anchored" for state in states)
    return {"anchored_share": anchored / max(len(cues), 1), "hard": float(len(hard)), "soft": float(len(soft)), "soft_share": len(soft) / max(len(cues), 1),
            "longest_unseen": float(longest_run([state["how"] not in ("anchored", "loose") for state in states])),
            "contradictions": float(sum(state["how"] == "contradiction" for state in states)),
            "loose_share": sum(state["how"] == "loose" for state in states) / max(len(cues), 1),
            "candidates": float(len(candidates)), "written": float(sum(candidate.get("written", False) for candidate in candidates)),
            "median_margin": float(numpy.median([anchor["margin"] for anchor in found["anchors"]])) if found["anchors"] else 0.0,
            "passages": float(len(found["passages"])), "scaled": float(found["scale"] != 1.0)}


def gap_minutes(gaps):
    return {minute for start, end, _ in gaps for minute in range(int(start // 60), int(end // 60) + 1)}


def in_file_order(verdict, order, cues):
    """Timings and line indices back in the file's own order."""
    def restored(timings):
        if timings is None:
            return None
        result = list(cues)
        for position, index in enumerate(order):
            result[index] = (timings[position][0], timings[position][1], cues[index][2])
        return result

    for candidate in verdict["candidates"]:
        candidate["moves"] = {order[line]: offset for line, offset in candidate.get("moves", {}).items()}
        candidate["lines"] = [order[line] for line in candidate["lines"]]
    return {**verdict, "timings": restored(verdict["timings"]), "proposal": restored(verdict["proposal"])}


def clean(text):
    text = MARKUP.sub("", (text or "").replace("\\N", " "))
    text = re.sub(r"^\s*-\s*", "", text, flags=re.M)
    return " ".join(text.split())
