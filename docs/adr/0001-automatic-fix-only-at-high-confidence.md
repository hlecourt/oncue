# Automatic fixes only at high confidence, audio sync verified on its own terms

The guard writes a subtitle without a person only when its confidence is high (80 or more); anything else becomes a proposal in Review. This holds for audio sync too, but ffsubsync's own "sync was successful" is not trusted as confidence: an audio sync is scored by the guard itself, by cutting the synced subtitle into up to eight parts of at least twenty lines and checking that each part, searched on its own against the speech detected in the sound, lands within 0.3 s of where the sync put it (the confidence is the share of parts that agree, and fewer than four parts stays low). Automatic audio writes stay off (`SUBTITLE_GUARD_AUDIO_AUTOMATIC`) until `calibrate-audio` on a real library, where reference tracks give the truth, shows no false positives.

## Considered Options

- Trust ffsubsync's success flag and write: rejected, it is the tool grading itself and its score is not even returned.
- Never write audio syncs automatically: rejected by the owner, who wants high-confidence audio syncs fixed without a person once the measure is proven.
- Score by how much of each line falls on speech, or by how far the best offset stands out: rejected, ffsubsync already maximises that same correlation, and on Tears of Steel the peak stood out as much for random times as for the true timing.
- Agreement of two voice detectors (webrtc and auditok): rejected, auditok marked 99.8% of a film with a music bed as speech.
