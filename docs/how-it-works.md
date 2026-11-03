# How OnCue decides

OnCue compares when each line of a downloaded subtitle starts and ends with when the lines of a subtitle track embedded in
the video start and end: the words do not matter, so the reference can be in any language, text or picture. A line counts as
matching when its start meets a reference start or its end meets a reference end within 0.3 s, so a translation that splits
one reference line in two, or joins two, still matches line by line.

1. It reads the reference timings from the Matroska Cues index (in about a millisecond), or with an ffprobe packet scan.
2. It measures how many lines already match, against what chance alone would give, overall and per 2-minute window.
3. It picks the frame rate ratio (same, 25 on 23.976 or the reverse), refines it when the best offset of each stretch of lines
   drifts steadily along the file (a speed error such as 1.5 %), then searches the offsets part by part with dynamic programming (0.05 s steps over
   +/-400 s, then 0.1 s over +/-1200 s). When the current timing already passes step 2, leaving it costs a lot: a part only
   moves on strong evidence, so a correct translation is never slid onto neighbouring lines. When it does not pass, the
   search follows the cuts freely.
4. A second, looser search proposes passages to move; one is moved only if it matches poorly where it is and matches where
   it goes far better than chance could explain over the offsets searched (a binomial test).
5. It runs the same search against the reference reversed in time, which has the same density but no relation to the
   subtitle, and keeps the fix only if it clearly beats that null and every part is supported by the reference.
6. It scores the result from 0 to 100; at 90 or more and in `fix` mode the fix is written after a backup, otherwise it waits
   as a proposal, or Bazarr is asked for another subtitle first.
7. With the optional text check (`ONCUE_TEXT_CHECK`), it then reads what the lines say. A multilingual sentence model matches
   each line with the reference line it translates (a line that translates two reference lines counts from the first), a
   monotonic chain keeps the consistent matches, and only runs of consecutive matches agreeing on one offset count. The text has
   the last word on the automatic band, and it runs on files that timing keeps as well, since timing alone cannot tell a line from
   its neighbour:
   - it confirms the current timing (90 or more) when 60 % of the lines are vouched for, at most 1.5 % sit 0.6 to 2.5 s off, nothing
     sits further off and no stretch of more than 4 lines escapes its matches;
   - it moves the passages that three or more agreeing matches put more than 2.5 s away, under the same conditions (a text fix,
     written in `fix` mode);
   - otherwise it caps the result at 89, marks the minutes it doubts, and offers the passages its matches put elsewhere as a
     proposal when timing has none of its own. It never confirms a timing proposal: those wait for a person.

8. With the optional speech check (`ONCUE_TEXT_SPEECH_CHECK`, on top of the text check), it also measures against speech onsets, for the subtitle and for the reference, since a reference can lead the speech. A
   move whose lines would sit more than a second off the speech, or that takes lines off the speech they sit on, is refused and not offered. A move is corrected by how far the reference sits from the speech over
   the file. A slow drift (the anchors on one line, better than one step) becomes a scale when most quarters of the file hold on the speech. A passage off by 0.65 to 2.5 s is a small move when the text's anchors and the
   speech measure the same move; it is written only at a gate a Learn-then-Test passed, and no gate has passed on the development data, so today a small move is only offered in Review; its first and last lines stay in the move only from two consecutive lines whose moved start meets a speech onset, the others wait in
   Review, and the speech is measured again on the lines that remain: under 25 lines or without a clear measure, nothing is moved. A frame-rate move is corrected for the reference's lead like any other.
   With the check on, a file is "verified" only when the speech measures its final timing clearly and no rolling window is off by more than 0.5 s: when the speech cannot measure the video, the text
   alone never verifies it, since a reference track that is itself off its audio would agree with a subtitle just as far off.
9. A proposal that waits for a person is checked first, the one a partial write leaves too (against the timings written). One from timing alone is corrected for the reference's lead, as a text move
   is, and described and scored as corrected. One whose lines would land more than a second off the speech, or a text proposal none of whose passages the safety model finds plausible, is withheld:
   nothing is offered, an older proposal for the same file is taken down, and the file goes to Review as `rejected` with the reason (never as in sync), so the check for another episode still runs.
10. A correction is written only when the chance that every passage in it is right (the product of their safeties) is 0.9 or more; under that it is a proposal. A small move counts at the safety its
   gate was tested at, not more.

## Decisions

| Decision | Meaning |
|---|---|
| `kept` | already in sync, nothing written |
| `fixed` | realigned and verified with high confidence, written, original backed up (in a dry run: would be written) |
| `proposed` | a fix was found but its confidence is not high, it comes from timing alone with the text check off, or it comes from the audio while automatic audio writes are off; nothing written, it waits for a person |
| `approved` | a proposal applied by a person, original backed up |
| `rejected` | could not be realigned with confidence, the end times are wrong, or the fix found was withheld because the text or the speech says it would make the file worse; with Bazarr, another subtitle is requested |
| `wrong-episode` | the subtitle matches a neighbouring episode; with Bazarr, another subtitle is requested, it is never moved |
| `deleted` | deleted by a person, a copy is kept in the backups |
| `unverified` | nothing to compare with, too few lines, the video could not be read, or the file changed during the check |

An audio sync has no reference track to compare with, so its confidence comes from the sound itself: the synced subtitle is cut
into up to eight parts of at least twenty lines, each part is searched on its own against the speech that ffsubsync's voice
detector finds, and the confidence is the share of parts that land within 0.3 s of where the sync put them (below four parts it
stays at 30). `oncue calibrate-audio PATH [N]` measures how far that can be trusted on your own library, without writing
anything: for videos whose reference track gives the truth, it shifts a copy of the subtitle by a known amount, syncs it to the
audio as if there were no track, and counts the confident results that are wrong.

Each decision carries a confidence from 0 to 100 and a band: `high` (90 and up), `medium` (60 to 89), `low` (below 60), `none`
(nothing accepted). It takes the weakest of these measures: the margin over noise, the margin of the worst 2-minute window over
its own noise, the weakest run of 8 to 20 consecutive lines (how unlikely so few matches are for this file), and for realigned
files the margin over the null alignment and how clearly every moved part beats its neighbours' offsets. The minutes of a weak
run are reported as weak minutes.

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
- Subtitles over 20 MB or 8,000 lines are not checked.
- Timing alone cannot see a passage slid by exactly one line, or a few lines of a translation that skips a short exchange; only
  the text check can, and only where the reference has text (a picture track needs OCR, see the configuration).
- The text check needs at least 20 lines of reference text; a line on its own is never moved by it, and stretches of short
  replies ("Yes.", "No.") give it little to anchor on.
