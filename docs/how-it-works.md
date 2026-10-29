# How the guard decides

subtitle-guard compares when each line of a downloaded subtitle starts with when the lines of a subtitle track embedded in the
video start: the words do not matter, so the reference can be in any language, text or picture.

1. It reads the reference timings from the Matroska Cues index, or with an ffprobe packet scan.
2. It measures how many lines already land within 0.3 s of the reference, against what chance alone would give.
3. If that is not clearly in sync, it picks the frame rate ratio, then searches the offsets part by part with dynamic
   programming (0.05 s steps over +/-400 s, then 0.1 s over +/-1200 s), using line starts and ends.
4. It runs the same search against the reference reversed in time, which has the same density but no relation to the
   subtitle, and keeps the fix only if it clearly beats that null and every part is supported by the reference.
5. It scores the result from 0 to 100; at 80 or more the fix is written after a backup, otherwise it waits as a proposal, or
   Bazarr is asked for another subtitle first.

## Decisions

| Decision | Meaning |
|---|---|
| `kept` | already in sync, nothing written |
| `fixed` | realigned and verified with high confidence, written, original backed up (in a dry run: would be written) |
| `proposed` | a fix was found but its confidence is not high, or it comes from the audio while automatic audio writes are off; nothing written, it waits for a person |
| `approved` | a proposal applied by a person, original backed up |
| `rejected` | could not be realigned with confidence, or the end times are wrong; with Bazarr, another subtitle is requested |
| `wrong-episode` | the subtitle matches a neighbouring episode; with Bazarr, another subtitle is requested, it is never moved |
| `deleted` | deleted by a person, a copy is kept in the backups |
| `unverified` | nothing to compare with, too few lines, the video could not be read, or the file changed during the check |

An audio sync has no reference track to compare with, so its confidence comes from the sound itself: the synced subtitle is cut
into up to eight parts of at least twenty lines, each part is searched on its own against the speech that ffsubsync's voice
detector finds, and the confidence is the share of parts that land within 0.3 s of where the sync put them (below four parts it
stays at 30). `subtitle-guard calibrate-audio PATH [N]` measures how far that can be trusted on your own library, without writing
anything: for videos whose reference track gives the truth, it shifts a copy of the subtitle by a known amount, syncs it to the
audio as if there were no track, and counts the confident results that are wrong.

Each decision carries a confidence from 0 to 100 and a band: `high` (80 and up), `medium` (60 to 79), `low` (below 60), `none`
(nothing accepted). It takes the weakest of three measures (the margin over noise or over the null alignment, the margin of the
worst 2-minute window, and for realigned files the share of lines in supported segments), then removes 10 points per weak minute.

## Accuracy

`benchmarks/torture.py` misaligns known-good tracks in 42 ways and measures every line against its true time, and exits non-zero
when a case fails. On a private corpus of 12 tracks of 420 to 720 lines (French, Japanese and German, one dialogue-heavy series),
502 of 504 runs ended as expected, with 97 to 100% of lines within one second when fixed: different cuts, ad breaks adding up to
600 s, offsets up to 600 s, framerate and speed errors up to 5%, sloppy timing, merged or split lines, and dense stretches.
Wrong-episode subtitles, random timings and offsets beyond +/-1200 s were rejected. These figures cannot be reproduced without
that corpus. To measure on a public one, built from the Blender open movie Tears of Steel and its six translations:

```
python benchmarks/tears_of_steel.py /tmp/tos
python benchmarks/torture.py /tmp/tos/work /tmp/tos/corpus.json
```

## Limits

- Cumulative offsets beyond +/-1200 s are rejected, not fixed; between +/-400 s and +/-1200 s a rejection costs a second, wider
  search (about 7 s instead of 2.5 s on a 4-core home server).
- Lines of a scene that exists in the subtitle but not in the video are placed as well as possible, not removed.
- Without any embedded subtitle track, timing cannot be verified; the ffsubsync fallback cannot fix a different cut.
- Subtitles over 20 MB or 20,000 lines are not checked.
