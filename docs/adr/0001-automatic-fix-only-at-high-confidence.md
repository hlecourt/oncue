# Automatic fixes only at high confidence, audio sync verified on its own terms

OnCue writes a subtitle without a person only when its confidence is high (90 or more since 2026-10-31, see below); anything else becomes a proposal in Review. This holds for audio sync too, but ffsubsync's own "sync was successful" is not trusted as confidence: an audio sync is scored by OnCue itself, by cutting the synced subtitle into up to eight parts of at least twenty lines and checking that each part, searched on its own against the speech detected in the sound, lands within 0.3 s of where the sync put it (the confidence is the share of parts that agree, and fewer than four parts stays low). Automatic audio writes stay off (`ONCUE_AUDIO_AUTOMATIC`) until `calibrate-audio` on a real library, where reference tracks give the truth, shows no false positives.

## Considered Options

- Trust ffsubsync's success flag and write: rejected, it is the tool grading itself and its score is not even returned.
- Never write audio syncs automatically: rejected by the owner, who wants high-confidence audio syncs fixed without a person once the measure is proven.
- Score by how much of each line falls on speech, or by how far the best offset stands out: rejected, ffsubsync already maximises that same correlation, and on Tears of Steel the peak stood out as much for random times as for the true timing.
- Agreement of two voice detectors (webrtc and auditok): rejected, auditok marked 99.8% of a film with a music bed as speech.

## Amended 2026-10-31: high starts at 90, measured against the text

A text-verified audit of a real library (48 subtitles, each French line read against the English line on screen) showed that 80 let
wrong writes through: a correct French file splits English sentences into two lines, scored 65 to 78 % on start coincidence, and was
"fixed" by sliding passages one line. Scoring that accepts a start or an end on the reference, a strong prior to keep a timing that
already passes, and a confidence taken as the weakest of five measures cut the wrong results at 90 or more, on the audited library
measured per version with every dense-read bad passage required to be repaired, from 17 of 43 to 5 of 19 (`benchmarks/text_truth.py`),
and on 800 damaged copies of in-sync files from 107 of 504 to 36 of 339. Automatic writes and the high band therefore start at 90.

90 is not yet a guarantee. What remains is what timing alone cannot see: passages slid by exactly one line, and short passages of 3 to
12 lines a few seconds off inside files kept at 90 or more. Reading the text of the reference (the next step) is meant to close them;
until then the cost of 90 is fewer automatic writes, as short passages stay proposals.
