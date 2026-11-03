# Grouped text alignment, and writes passage by passage

ADR 0006 made the optional text check the last word on automatic writes, but on the stricter evaluation contract (every changed cue
judged, writes, proposals and verified keeps apart) its chain of best matches wrote little and still wrote wrong passages: a single
"confidence" mixed two questions that need separate answers. The text check is now built on:

- **A gap-aware grouped alignment** (`text_align.py`): one monotonic dynamic programme over 1:1, 1:2 and 2:1 groups of lines, where a
  subtitle line or a reference line may stay unmatched for free (untranslated dialogue, lines the reference lacks). Scores are z-scores
  per row and column, so a line that resembles everything earns nothing; each match keeps its margin over the competing lines nearby.
  Group vectors are built from the line vectors, so only lines are embedded (half the cost of embedding joined pairs).
- **Passages from anchors**: clear matches vote for an offset (after a frame-rate scale found from the median drift of the anchors); a
  Viterbi pass over candidate offsets cuts passages, cheaper on a dialogue pause and cheaper still on a long silence. A lone match that
  disagrees stays a contradiction (a line moved past its neighbours, which an in-order alignment cannot match, is one too).
- **Seams decided on evidence or not at all**: the lines between two passages go to one side only on their own weak match, a start meeting
  a reference start, speech under them (audio read only around seams), and order; otherwise they stay undecided and in Review.
- **Two outcomes.** "This change is safe": each candidate passage gets a calibrated probability (logistic model over its evidence, isotonic
  calibration, fitted on one third of the episodes and calibrated on another, one model per sentence-model family) and is written only
  above 0.985 and only if it keeps the order with the lines around it. "The file is verified": only when no line is in doubt at all, a
  rule that held on every development file meeting it, where a fitted model let wrong files through. A partial write keeps the file in
  Review with the minutes nothing vouched for.
- **Untranslated stretches** (reference dialogue no line translates nor faces) are reported with their minutes and listed in Review; no
  text is ever added.

## Measured (2026-11-01, evaluation contract 1.0.0, multilingual-e5-small int8)

| | Timing only | Text check before (LaBSE) | This design (E5 int8) |
|---|---|---|---|
| Development synthetic writes, failing | 339, 58 | 19, 13 | 333, 1 |
| Development library writes, failing | 2, 2 | 0, 0 | 1, 1 (0.2 s outside a sparse label) |
| False verifications (development) | 250 | 0 | 0 |
| Bad cues repaired by writes (development) | 123,646 | 3,443 | 140,736 |
| Held-out writes, failing (never tuned on) | 202, 14 | 112, 78 | 159, 0 |
| Held-out false verifications | 107 | 11 | 0 |
| Held-out bad cues repaired by writes | 52,395 of 62,046 | 9,404 | 48,254 |
| Held-out gap detection, recall and false alarms | (structural rule) 0.96, 524 | none | 0.91, 6 |

## Consequences

- Files are rarely green: most keep at least a few lines the text cannot vouch for, and stay in Review even after a safe partial write.
- Whole-file repairs are fewer than timing-only's (195 versus 274 development synthetic files, 117 versus 184 held out): seams the evidence
  cannot settle are left for a person rather than guessed.
- The calibrations belong to the model family they were fitted with (E5 and LaBSE); any other model never authorises a write.
- Cost on the 4-core home server, 2 threads, E5 int8: about 4 s and 0.5 GB for a 22-minute episode; LaBSE int8 about 12 s and 0.9 GB.
