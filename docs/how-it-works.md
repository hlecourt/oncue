# How the guard decides

subtitle-guard compares when each line of a downloaded subtitle starts with when the lines of a subtitle track embedded in the
video start: the words do not matter, so the reference can be in any language, text or picture.

1. It reads the reference timings from the Matroska Cues index, or with an ffprobe packet scan.
2. It measures how many lines already land within 0.3 s of the reference, against what chance alone would give.
3. If that is not clearly in sync, it picks the frame rate ratio, then searches the offsets part by part with dynamic
   programming (0.05 s steps over +/-400 s, then 0.1 s over +/-1200 s), using line starts and ends.
4. It runs the same search against the reference reversed in time, which has the same density but no relation to the
   subtitle, and keeps the fix only if it clearly beats that null and every part is supported by the reference.
5. It scores the result from 0 to 100, and writes the fix after a backup when the result can be trusted.

## Decisions

| Decision | Meaning |
|---|---|
| `kept` | already in sync, nothing written |
| `fixed` | realigned and verified, original backed up |
| `rejected` | could not be realigned with confidence; in hook mode Bazarr is asked for another subtitle |
| `wrong-episode` | the subtitle matches a neighbouring episode; reported, not blacklisted |
| `audio-synced` | no embedded track; synced to the audio with ffsubsync, not verified |
| `unverified` | nothing to compare with, or too few lines (forced subtitles) |

Each decision carries a confidence from 0 to 100 and a band: `high` (80 and up), `medium` (60 to 79), `low` (below 60), `none` (nothing accepted). It takes the weakest of three measures (the margin over noise or over the null alignment, the margin of the worst 2-minute window, and for realigned files the share of lines in supported segments), then removes 10 points per weak minute. On the benchmark, accepted results are almost all `high`; the few `medium` or `low` ones are the cases that really are less precise, such as very sloppy source timing.

`needs-human` lists every subtitle whose last decision is not settled, has weak minutes, or has low confidence.

## Accuracy

`benchmarks/torture.py` misaligns known-good tracks in 42 ways and measures every line against its true time. On 12 tracks of 420 to 720 lines (French, Japanese and German, one dialogue-heavy series), 502 of 504 runs end as expected, with 97 to 100% of lines within one second when fixed, including different cuts, ad breaks adding up to 600 s, offsets up to 600 s, framerate and speed errors up to 5%, sloppy timing, merged or split lines, and dense stretches. Wrong-episode subtitles, random timings and offsets beyond ±1200 s are rejected. The two misses are fragmented dense stretches that are realigned correctly but rejected as a precaution. `benchmarks/tears_of_steel.py` builds a public corpus from the Blender open movie Tears of Steel.

## Limits

- Cumulative offsets beyond ±1200 s are rejected, not fixed; between ±400 s and ±1200 s a rejection costs a second, wider search (about 7 s instead of 2.5 s on a 4-core home server).
- Lines of a scene that exists in the subtitle but not in the video are placed as well as possible, not removed.
- Without any embedded subtitle track, timing cannot be verified; the ffsubsync fallback cannot fix a different cut.
