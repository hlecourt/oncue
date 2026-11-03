# Refine passes after the first write, and recognised speech at seams as evidence

ADR 0007 wrote a passage only when its calibrated safety was high and it kept the order around it. On the development set most of the
material it left was right in its proposal: 59,000 wrong lines sat in passages the safety model refused, 25,600 more in passages refused
only because undecided lines next to them would have ended up out of order. A passage's safety was judged once, with its seam lines, so one
doubtful boundary line refused a whole passage, and two halves of one move split by a lone contradiction were judged apart.

The text check now runs up to three passes. The first is ADR 0007's. Each later pass looks only at what is left and tries targeted
alternatives, each judged by the same calibrated "this change is safe" and the same order rule, and the result of each pass goes through
the caller's output check (the guard's rendered-file check) or the pass is undone:

- the core of a refused passage, between its first and last anchored lines (its seam lines left in Review);
- neighbouring passages on practically one offset (within 0.25 s) merged into one;
- a passage extended over the unplaced lines its move would jump, never over a line next to a passage written on another offset;
- the undecided lines between a written passage and its neighbour settled again with one cut, and the undecided lines right next to a
  written passage sent with it, both only when each line's own text reads clearly better at the new place.

A later pass never moves a line an earlier pass wrote, a line the text verified in place, or a line the first pass kept in place on purpose
(misordered in the file, or reading clearly where it is). Every alternative's features are measured for its own move, and nothing written
counts as evidence for what follows. The structural alternatives (merge, extension) run in the second pass only; a third pass runs only
with new evidence (recognised speech) and only on smaller regions (cores, seam lines). The loop stops when a pass writes nothing.

The first pass also changed: a seam line's side now weighs how well it reads against the reference lines around each place, never its best
match anywhere in the file (a phrase said twice pulled one development line across a seam), and lines past the last anchor follow a
whole-file move when the passages on that offset, together, span most of the file.

"This change is safe" is refitted: weights constrained to the direction each feature can only push, isotonic tables calibrated apart for
first-pass candidates and refine alternatives, on candidates produced exactly as the loop produces them with the installed model, and a
candidate with any feature outside the envelope of the fitting and calibration rows (or a feature the model expects and the check no longer
measures) is refused. "The file is verified" no longer passes a file on a passage off by 0.5 to 1 s, lines shown for a blink or ending far
from every reference line, unreadable lines with no reference start under them, or written lines with no evidence of their own.

Recognised speech (optional, `ONCUE_TEXT_SEAM_SPEECH`, off by default): the refine passes may ask for a few seconds of audio around the
lines still in doubt, at their current place and at the place a written passage would take them, at most 90 s per file. faster-whisper base
int8 transcribes the audio stream in the reference's language, with that language pinned, and the words are compared with the subtitle line
(across languages) and with the reference line the text matched it to (same language). The agreement is seam evidence only; it never writes
on its own. Seam speech activity (`ONCUE_TEXT_SEAM_AUDIO`) is now off by default: it sent a development seam line to the wrong side.

Safety around the write: the shared output check now bounds a pre-existing overlap or inversion (it may not grow) and runs on the rendered
bytes read back. With the text check off, a timing fix never reaches the automatic band: it stays a proposal.

The guard and the benchmarks share one IO-free decision (`guard.decide`). After a partial write, what the text proposes but could not vouch
for is saved as a proposal on the version just written, so Review shows it. The report row keeps the text verdict, and Review holds every
keep under the automatic band and every unsettled text verdict. Untranslated stretches come out as `[from, to, lines]`.

## Measured

Evaluation contract 1.0.0, development labels version 2 (corrections.json and gaps.json `_version` 2), held-out manifest of 2026-11-01,
multilingual-e5-small int8, frozen code, the harness unmodified:

| | Timing only | Previous engine (ADR 0007) | This design |
|---|---|---|---|
| Development synthetic writes, failing | 339, 58 | 346, 1 | 492, 3 |
| Development library writes, failing | 2, 2 | 1, 1 | 3, 1 (the same 0.2 s case) |
| Development synthetic bad cues repaired, files fully repaired | 123,841, 274 | 143,953, 200 | 193,857, 328 |
| Held-out writes, failing | 202, 14 | 159, 0 | 170, 1 |
| Held-out false verifications | 107 | 0 | 0 |
| Held-out bad cues repaired, files fully repaired | 52,395, 184 | 48,254, 117 | 54,624, 135 |

The refine passes add, on development, 53 writes, 19,313 repaired cues and 31 whole files with no new failing write; on held-out 5 writes,
4,523 cues and 8 files, no new failing write. Every failing write comes from a first-pass candidate in the 0.993 calibration bucket (a
boundary line or two included with a passage). Recognised speech added one development write and two whole files, and one failing write
(an extension whose seam evidence changed): it was never part of the calibration, so it stays off by default.

## Consequences

- The release gate is not met: three development and one held-out failing write remain at the 0.985 threshold, all boundary lines of a
  first-pass candidate the model puts at 0.993.
- Rejected after measuring: snapping lines to their own anchor (the passage offset is right far more often, on synthetic and library
  files alike); the timing judge's own timings as a safety feature (more coverage but more failures at the write threshold).
- Not done: a time prior in the alignment, 24/23.976 frame-rate pairs (too close to 1 to tell from noise without data), file-level risk
  aggregation (the minimum passage safety still drives the displayed score, which is not the probability that every change is right).
- The safety model is refitted on every candidate of every pass as the loop makes them, on development episode thirds only.
