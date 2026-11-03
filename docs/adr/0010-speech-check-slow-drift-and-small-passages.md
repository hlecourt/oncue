# A speech check, slow drift as a scale, and small passages written only when text and speech agree

Evaluation contract 1.1 reads a line as right within 0.5 s of its target and fails a file with a systematic residual above 0.5 s. Under it, ADR 0009's engine had two weak points: offsets under a second (a line is never nudged by less than one second, and a file off by 0.75 s only went to Review), and slow drift, where the passages cut a slope into steps: every failing drift write on development had a stepped residual of 1 to 1.9 s. The English reference track can also lead the speech, so a move onto it can land early.

The text check can now measure against the speech (`speech_check`, `ONCUE_TEXT_SPEECH_CHECK`, off by default). A speech onset starts a run of speech after 0.25 s of silence; the offset of a set of starts is the peak of the histogram of start-onset lags within 3 s, smoothed at 50 ms, less a -0.09 s baseline measured on official tracks. It reads the centre channel (a mono downmix without one) of the reference's language with webrtcvad, with no PyTorch or Whisper, and webrtcvad is imported only there, so the Bazarr hook stays NumPy only.

- **Refusal.** A move whose lines would sit more than 1 s off the speech, or that takes lines off the speech they measurably sit on, is refused and not offered as a proposal.
- **Reference lead.** A move is corrected by how far the reference sits from the speech over the file, when that is settled and at least 0.15 s.
- **Slow drift.** Blocks of 12 anchors on one line across most of the file, within 0.3 s for 85 % of the blocks, explaining them 1.5 times better than a single step, worth at least 0.5 s over the file, become a scale. The move is written only if at least 3 of 4 quarters of the file are measurable and none is more than 0.5 s off once corrected. Without speech, no drift is read.
- **Small passages.** The one-to-one anchors' offsets are cut into levels by a finer Viterbi pass. A level 0.65 to 2.5 s off is a candidate only when the speech measures the same move within 0.25 s under the same lines. At most 2 such moves per file, none in a file with 2 or more passages over 2.5 s away, and none covering most of the file. Text alone never makes one, and neither does speech.
- **Verified.** A rolling window of 40 lines settled more than 0.5 s off keeps the file out of "verified". With the check on and no settled measure of the whole file, a file is never verified.

Settled means at least 25 lines and a prominence of 5 (3.5 for a candidate, and for a drift's quarters). On in-sync development bases, a measure of 30 lines at prominence 5 or more was over 0.5 s wrong in 0.8 % of 1,260 trials, of 40 lines in 1.1 %, of 20 lines in 6.7 %.

Safety models are refitted on development thirds with contract 1.1 labels, on the harness's cases plus the small damages built on development bases with the held-out generator's settings (the harness's development set has none). Gates by Learn-then-Test on the other two thirds, alpha 0.015 and delta 0.025 per pass, fixed sequence from strict to loose, stopping at a step the test does not reject or that adds a failure: first pass 0.97, refine 0.95, small passages at prominence 10. The gate sweep for small passages was run with whole-file moves still in; the code then lost them (below). Files of one episode are correlated, so this is a statement about development data, not production.

## Measured

Contract 1.1.0, development labels version 3, multilingual-e5-small int8, frozen code, harness unmodified. Small damages on development come from `dev_small.py`, outside the harness, on the same bases.

| | ADR 0009 engine | This design |
|---|---|---|
| Development library writes, failing | 6, 0 | 6, 0 |
| Development old damages writes, failing, whole files | 539, 0, 374 | 550, 1, 384 |
| Development small damages writes, failing, upper bound, whole files | 94, 3, 8.0 %, 48 | 185, 1, 2.5 %, 153 |
| First held-out old damages writes, failing, whole files | 197, 1, 183 | 198, 2, 182 |
| First held-out small damages writes, failing, upper bound, whole files | 170, 8, 8.3 %, 119 | 293, 6, 4.0 %, 231 |
| First held-out false verifications | 0 | 0 |
| First held-out correct verified keeps | 72 | 0 |

Written files on calibration thirds of development, old and small together: 595, 0 failing, Clopper-Pearson 95 % upper bound 0.5 %; third 0: 140, 2 failing. False verification 0 is partly structural: nothing is accepted on development, and on held-out no keep is accepted any more.

## Consequences

- The release gate is not met: failing writes remain, and the 3 % bound on small damages holds on development only.
- All 72 verified keeps lost on the first held-out set are Tears of Steel: its measure does not settle, so the check fails closed. Files that short stay in Review with the check on.
- A file off as a whole by under a second stays in Review, including the one real development file with that damage.

## Considered Options

- Whole-file small moves when text and speech agree: on development small damages 295 writes, 5 failing, 249 whole files, and the real Jujutsu Kaisen 0 file repaired for 1,372 lines. Dropped because that write counts as failing: two lines labelled apart move with the file. It is the largest repair left on the table, and a person's call.
- Leaving strong contradicted anchors behind in a whole-file move: these are mostly right lines; failing writes went up and whole files down.
- Refusing any move more than 0.5 s off the speech: it refused right moves on a base itself 0.6 s off the speech. The limit is 1 s, or closer than now.
- Dropping small-passage cuts at prominence 8: 4 more failing files in calibration.
