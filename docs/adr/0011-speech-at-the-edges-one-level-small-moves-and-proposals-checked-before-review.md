# Speech at the edges of small moves, one level per small move, the reference's lead everywhere, and proposals checked before Review

ADR 0010 left five weak points, each traced to one cause on development data (the first held-out set and the second, burnt by its blind run, are development data now):

- **Real files that do not write.** Two mechanics refused moves the text vouched for. A refine pass was undone whole when the output check refused the combination, so one candidate with an overlap took
  right ones down with it (The Good Place S04E06: 16 right lines refused because another passage would overlap its neighbour). And a whole file off by under a second was dropped as "several levels"
  when stray anchors cut one move into pieces: Jujutsu Kaisen 0 (French, 1,374 lines, 0.95 s late) gave 8 moves, all on -0.93 to -1.03 s, all with settled speech.
- **Edges of small moves.** At under a second the text cannot tell an edge line that belongs to the move from one in place whose translation starts late: the harmed edge lines had their own anchors
  agreeing with the move.
- **Frame rate on picture references.** The 100 S03 English PGS tracks lead the speech by about 0.35 s. A move is corrected by that lead, except that the correction was skipped for any scale, so every
  25 fps passage landed 0.35 s early (S03E07: 131 lines wrong).
- **Harmful proposals.** With the text unsettled, the timing judge's own proposal went to Review unchecked; on The 100 it copies the leading reference (S03E07: 466 lines changed, most by under 0.5 s).
  Text proposals made only of candidates the model finds implausible (safety near 0) were offered too.
- **No verified keep on Tears of Steel.** 76 lines over 136 speech onsets measure the right offset (-0.18 s) at prominence 3.6 to 3.9, under the file threshold of 5, for the reference track as much as
  for the subtitles: the check had no opinion, so it never verified.

## Decision

- `select` sets aside a candidate the output check refuses on its own and tries again with the others; only a refusal of the combination still undoes the pass. The output check still sees every write.
- A small move's first and last lines stay in it only from the first and the last pair of consecutive lines whose moved start meets a speech onset (within 0.25 s, and at least 0.1 s closer than
  where the line starts now); lines outside wait in Review. Chosen on the calibration thirds among 1 to 3 consecutive lines and 0.10, 0.15, 0.25 s.
- Small moves on one level (within 0.1 s) count as one move, and a move of most of the file is no longer refused: with its edges trimmed by the speech, it is written like any other small move.
- The small-move gate (speech prominence) is chosen again by Learn-then-Test on the calibration thirds, files as units, alpha 0.015, delta 0.025, strict to loose: 10 to 5.
- The reference's measured lead corrects frame-rate moves too (a slow drift keeps its own check), and the timing judge's proposal when the text check runs.
- A proposal that waits for a person is withheld, the file staying in Review as it is with the reason, when the lines it changes would land more than a second off the speech, or when it is a text
  proposal whose best candidate has a safety under 0.1 (chosen on the calibration thirds among 0.05, 0.1, 0.2, 0.5).
- When the speech was read but the reference track's own starts (25 lines or more) do not reach the file prominence, the speech cannot measure the video and has no say on "verified", as when the
  check is off. With no audio read at all it still never verifies.

Every invariant stays: frozen lines, timing alone never writes, the shared output check in `subtitles.write_subtitle`, `guard.decide` IO-free, the Bazarr hook NumPy only, the envelope refusal, fail
closed. No setting is added.

## Measured

Contract 1.1.0, development labels version 3, multilingual-e5-small int8, harness unmodified, homeserver, 2 jobs. Engine 4 figures are iteration 4's stamped files (dev v3 rerun bit for bit).

| Set | Writes | Failing | Upper bound | Fully repaired | Repaired cues | False verifications | Verified keeps | Harmful proposals |
|---|---|---|---|---|---|---|---|---|
| Development v3 (1,138) | 550 / 552 | 1 / 2 | 0.9 % / 1.1 % | 384 / 384 of 1,001 | 193,877 / 194,389 | 0 / 0 | 0 / 0 | 291 / 188 |
| Development small damages (703) | 185 / 260 | 1 / 0 | 2.5 % / 1.2 % | 153 / 138 of 407 | 56,975 / 77,740 | 0 / 0 | 0 / 0 | 98 / 88 |
| First held-out set (1,550) | 491 / 573 | 8 / 5 | 2.9 % / 1.8 % | 413 / 382 of 900 | 129,470 / 163,853 | 0 / 0 | 0 / 54 | 99 / 61 |
| Second held-out set, damaged (279) | 74 / 97 | 4 / 0 | 11.9 % / 3.0 % | 44 / 41 of 162 | 33,690 / 38,466 | 0 / 0 | 0 / 0 | 118 / 48 |
| Second held-out set, natural (8) | 0 / 0 | 0 / 0 | - | 0 / 0 of 1 | 0 / 0 | 0 / 0 | 0 / 0 | 5 of 6 / 1 of 3 |

Engine 4 / engine 5. The new development failure is Jujutsu Kaisen 0 [original]: 496 lines repaired by small moves, one line wrong by its label, a label the speech contradicts (it is labelled 0.52 s
after its own start where its neighbours are labelled 0.95 s before theirs, and the speech onset sits where the move puts it). The first held-out failures left are 4 single edge lines of 30-line passages (The Dog Stars) and one
Tears of Steel line at 25 fps, as before. Fully repaired files drop where small moves now stop at edges without speech under them. The small-move gate: on the calibration thirds, 658 written files,
120 with small moves, 1 failing (the line above) down to prominence 5; at 4.5 a second failure. Edge trimming: 0 of 75 calibration candidates and 0 of 44 check candidates still harming (5 and 4 before).
Cost: median 5.7 s per development case at 2 jobs (engine 4: 6.3 s on the same server and load); the speech read code is unchanged, measured at 45.6 s for a 42-minute episode at 2 cpus.

## Considered Options

- Order tests with the output check's own tolerances (any swap, 0.5 s of new overlap) in the extension and order rules: on dev it gained nothing and lost a fully repaired file (an extension took 7
  more lines with no evidence of their own and fell under the refine gate). Rejected.
- Withholding a proposal when the text reads a share of its moved lines in place, or when the speech has the changed lines in place and the change brings them no closer: on dev both withheld more
  helpful proposals (repairing more lines than they harm) than harmful ones. Rejected.
- Lifting the "file in a dozen pieces" refusal: on dev library files the model's own gate then let 3 unsafe candidates of 10 through. Kept.

## Consequences

- Files fully repaired by a small move fall where the file's first or last lines have no speech under them: those lines stay in Review while the rest is written.
- The 3 % bound on written files is a statement about development data. Every set used here has been seen; any change made for these results needs another untouched set to be judged.
