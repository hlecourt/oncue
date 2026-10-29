# Automatic fixes only at high confidence, audio sync verified on its own terms

The guard writes a subtitle without a person only when its confidence is high (80 or more); anything else becomes a proposal in Review. This holds for audio sync too, but ffsubsync's own "sync was successful" is not trusted as confidence: an audio sync is scored by the guard itself, against speech onsets detected in the sound (matched share before and after, mirrored null test) and the agreement of two voice detectors, and automatic audio writes stay off until a calibration set of films with known offsets shows no false positives.

## Considered Options

- Trust ffsubsync's success flag and write: rejected, it is the tool grading itself and its score is not even returned.
- Never write audio syncs automatically: rejected by the owner, who wants high-confidence audio syncs fixed without a person once the measure is proven.
