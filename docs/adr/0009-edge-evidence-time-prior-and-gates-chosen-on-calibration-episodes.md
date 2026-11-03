# Edge evidence, a soft time prior, and write gates chosen on calibration episodes

ADR 0008 left four failing writes, all passages at one calibration value taking in a line or two beyond the true passage, and a 0.995 threshold that removed them only after looking at held-out. It also lost whole files on lines past the last anchor and on seams.

- **Edges.** A passage's line at a seam stays in the move only with evidence of its own at that place: its own anchor, or a strong match with a reference start there, each reading clearly better than at the other places the line could take (where it is, the passages either side, written or not). A line whose own match, however weak, puts it elsewhere has none. The others are trimmed and offered again by the refine passes as separate alternatives.
- **Moves.** Neighbouring passages within 0.25 s are one candidate, each line keeping its own passage's offset. Lines past the file's last anchor (or before the first) are offered with the passage beside them, with their distance and evidence as features. A candidate blocked only by order is retried with what each pass adds; when a longer alternative breaks the order, the ones it overlapped get their turn.
- **Refusals before the model.** Unread lines interleaved through a short move with one reading better where it is (the correct result often cannot be written in file order), a file in a dozen pieces or more, a short move mostly unanchored with a line reading clearly better where it is.
- **Alignment.** A second dynamic programme adds a time prior from the first one's passages: a bonus on a place a neighbouring passage predicts, a capped cost far from all of them, so strong text always wins. Two lines of one sentence lose the group penalty. Group vectors are still built from line vectors.
- **Safety.** Separate logistic models for first-pass candidates and refine alternatives (fitted on one third of the development episodes, tables on another). The write gate is on the raw score, chosen by Learn-then-Test on the other two thirds with files as units and the loss "a written file holds a wrong moved line": fixed sequence from strict to loose, stopping at the first failure, alpha 0.015 and delta 0.025 per pass. The displayed score comes from the product of the written passages' safeties, not the weakest alone.

## Measured

Contract 1.0.0, labels version 2, multilingual-e5-small int8, frozen code, harness unmodified. Gates: first pass 0.97, refine 0.95. Guarantee under exchangeable files: with probability at least 0.95, the share of written files holding a failing write is at most 3 %. Calibration: 431 written files, 0 failing, Clopper-Pearson upper bound 0.69 %. Files of one episode are correlated, and alpha was set knowing how many files calibration offers, so this is a statement about development data, not production.

| | Timing only | ADR 0008 | This design |
|---|---|---|---|
| Development writes, failing | 341, 60 | 495, 4 | 540, 0 |
| Development half A / B whole files | 159 / 115 | 190 / 138 | 216 / 159 |
| Held-out writes, failing | 202, 14 | 170, 1 | 197, 1 |
| Held-out false verifications | 107 | 0 | 0 |
| Held-out bad cues repaired, whole files | 52,395, 184 | 54,624, 135 | 57,024, 183 |

## Consequences

- The release gate is not met on held-out: one failing write (Tears of Steel, Japanese, 25 fps, one line) and one whole file short of timing-only. Held-out has now been seen by this design; any change made for these results needs another untouched set to be judged.
- Anchors right, library pairs: 90.8 % before, 92.6 % with the prior and sentences; weak groups 77.0 % to 88.1 %. Whole files barely move with the prior switched off on the same model; it is kept for anchor precision.
- Rejected after measuring: 24/23.976 as a scale. In-sync anchors measure within 0.0005 of 1, but cuts bend the slope the same way: 22 false detections on development, one of them a failing write.
- LaBSE has no calibration in this version: it fails closed until refitted. `ONCUE_WRITE_SAFETY` in the benchmark adapter no longer moves the gate.
- The prior doubles the alignment's matrices: 1.1 GB on a film with 6,966 reference lines.
