# Verified needs the speech, a real file-safety floor, and small moves written only at a gate that passed

Two independent audits of ADR 0011's engine found holes that let a wrong result leave Review or carry a confidence it had not earned:

- **Verified on the text alone.** When the reference track's own starts did not stand out against the speech, the speech abstained and the text alone could verify the file. A reference that is
  itself off its audio (a bad remux) measures exactly like that, and a subtitle that follows it was verified. On the first held-out set, with each identity case's reference and subtitle moved
  together against the unchanged audio by +0.6, +1, +2.5, +4, +6 and -2.5 s, ADR 0011's engine verified 9 of the 50 cases at every shift (54 false verifications, all Tears of Steel).
- **No real floor.** `confidence.above()` mapped any written correction onto 90-100, so `FILE_SAFETY_FLOOR` (0.9) never kept a correction out of the automatic band.
- **Small moves judged on another object.** The speech evidence of a small move (prominence, lines, share) was measured before its extension and edge trimming, and kept after them: a move
  trimmed to a few lines kept the evidence of thirty. `speech_edges()` accepted a single line at either end and raised StopIteration when only the last line had speech under it. Every small move
  carried a constant 0.99, whatever gate it passed.
- **The small-move gate statistics.** ADR 0011's Learn-then-Test counted small-move failures over every written file (658, of which 120 held a small move), split thirds by language track (one
  video in several thirds), and checked on a third the inherited safety model was fitted on.
- **Proposals.** The proposal a partial write leaves was saved with no vetting and with the written part's confidence; a vetoed proposal left an older saved one in place; a timing proposal was
  described before its lead correction; a withheld proposal was decided `kept`, shown as "In sync", and skipped the other-episode check.
- **Speech read.** The read timeout was a `wait()` after a blocking read, so it never fired; the audio stream fell back to the first one when no tag matched, so a dub could be measured.

## Decision

- With the speech check on, "verified" needs a settled speech measure of the final timing and no rolling window off by more than 0.5 s. When the speech cannot measure the video, nothing is
  verified. The text alone verifies only with the check off.
- A correction whose file safety (the product of its passages' safeties) is under 0.9 scores under 90: it is a proposal.
- A small move's speech evidence is measured again on the lines it will write, after extension and trimming; under 25 lines, under prominence 3.5 or no longer agreeing with the text, the move
  is dropped. Its edges need a real pair of consecutive lines on the speech at each end. Its safety is one less the risk its gate was tested at (0.985 at alpha 0.015).
- The small-move gate is tested again by Learn-then-Test with the file holding a small move as the unit and a wrong line a small move wrote as the loss, thirds by canonical title (one video with
  all its languages, editions and damages), the check third holding no title the inherited model was fitted or calibrated on, alpha 0.015, delta 0.025, fixed sequence strict to loose. No gate passed: at the strictest gate (prominence 14) the calibration titles hold
  133 files with a small move, 2 failing (p 0.68); at 5, 216 files and 6 failing (p 0.95). With alpha 0.015 even a failure-free calibration needs 244 such files. So a small move is
  offered in Review and never written (`FINE_GATES` holds no prominence for e5). A small move whose final lines do not settle stays an offered candidate, so its lines are not handed to the
  passage path either (handing them over wrote 3 wrong edge lines on a Tears of Steel case during development).
- The proposal a partial write leaves is vetted like any other against the timings written, and saved with a proposal's confidence (capped at 89) and its own description. A withheld proposal is
  decided `rejected` with the reason, takes down an older saved proposal of the same file, and the other-episode check runs. A timing proposal's description and score are computed after its lead
  correction.
- The speech read is killed by a timer at its time limit. The speech check reads only an audio stream known to be in the reference's language: several streams and none tagged with it, or a single
  stream tagged with another language, give no opinion. Stream languages are read from ffprobe's JSON, so an untagged stream no longer shifts the index.

Every invariant stays: frozen lines, timing alone never writes, the shared output check in `subtitles.write_subtitle`, `guard.decide` IO-free, the Bazarr hook NumPy only, the envelope refusal,
fail closed (now without the speech exception). No setting is added.

## Measured

Contract 1.1.0, development labels version 3, multilingual-e5-small int8, harness unmodified. Engine 4 / engine 5 (ADR 0011) / this design. Engine 4 and 5 are their stamped runs; this
design ran on the homeserver (4 jobs) and, for development small damages, the second held-out set and three videos of the first, on one Mac core after an engine 5 sample (38 cases) and a
sample of this design (81 cases) gave per-case results identical to the server's. By title: one video with all its languages, editions and damages, failing when any of its writes fails.

| Set | Writes | Failing | Upper bound | Failing titles | Fully repaired | Repaired cues | Harmful proposals |
|---|---|---|---|---|---|---|---|
| Development v3 (1,138) | 550 / 552 / 551 | 1 / 2 / 1 | 0.9 / 1.1 / 0.9 % | 1 of 33 / 2 of 34 / 1 of 34 | 384 / 384 / 384 | 193,877 / 194,389 / 193,893 | 291 / 188 / 189 |
| Development small damages (703) | 185 / 260 / 159 | 1 / 0 / 0 | 2.5 / 1.2 / 1.9 % | 1 of 28 / 0 / 0 | 153 / 138 / 134 | 56,975 / 77,740 / 51,196 | 98 / 88 / 102 |
| First held-out set (1,550) | 491 / 573 / 420 | 8 / 5 / 1 | 2.9 / 1.8 / 1.1 % | 3 of 6 / 2 of 6 / 1 of 6 | 413 / 382 / 367 | 129,470 / 163,853 / 126,615 | 99 / 61 / 84 |
| Second held-out set, damaged (279) | 74 / 97 / 60 | 4 / 0 / 0 | 11.9 / 3.0 / 4.9 % | 3 of 8 / 0 / 0 | 44 / 41 / 41 | 33,690 / 38,466 / 30,712 | 118 / 48 / 56 |
| Second held-out set, natural (8) | 0 / 0 / 0 | 0 / 0 / 0 | - | - | 0 / 0 / 0 of 1 | 0 / 0 / 0 | 5 of 6 / 1 of 3 / 1 of 3 |

False verifications are 0 everywhere for all three. Results out of Review: on the first held-out set 65 / 63 / 63 writes and 0 / 54 / 0 keeps (all Tears of Steel), none elsewhere. The two
failing writes left are older than engine 5: Perfect Days (a 40-line passage 14 s early, 3 lines) and Tears of Steel Japanese at 25 fps (1 line). Shifted references (first held-out identity
cases, 6 shifts): 54 false verifications of 300 for engine 5, 0 for this design. No write of engine 5 fell under the file floor on the development sets but one (development small
damages, file safety 0.873). The proposal a partial write leaves, if accepted (development small damages, scored outside the harness): engine 5, 95 such proposals, 81 harmful; this design,
21, 10 harmful (1 withheld by the new vetting; the drop is mostly fewer writes, the vetting itself is not enough). Second held-out damaged: engine 5, 43 with 36 harmful; this design, 11 with 4 harmful (2 withheld). Withheld proposals: 177 decided `kept` by engine 5 (105 of them subtitles of another video) are `rejected` now; 20 remainders of partial writes are withheld.

## Considered Options

- Narrowing the abstention instead of removing it: let the text verify when the reference's own starts show a weak bump (prominence 3.5 to 5) within 0.5 s of zero. On the shifted first held-out
  cases it kept the 9 in-sync Tears of Steel keeps but verified the 9 cases shifted by +0.6 s (the bump moved to about +0.4 s while the file sat 0.6 s off). Rejected: a video the speech measures
  this weakly cannot tell 0.6 s.
- Keeping ADR 0011's gate and its unit. Rejected by both audits: its denominator counted files with no small move.

## Consequences

- Fewer writes: small passage offsets (0.65 to 2.5 s) and small whole-file offsets go to Review with a proposal, until a gate passes on enough independent titles. Harmful proposals
  rise where small moves are now offered instead of written.
- Tears of Steel is never verified as it is: its speech is too weak to measure, and a file the speech cannot measure is not verified.
- The upper bounds are statements about development data that every iteration has seen; the per-title bounds (13 to 58 %) say how little 6 to 34 videos can prove.
