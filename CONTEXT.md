# subtitle-guard

Checks the timing of external subtitles against the video they belong to, fixes it on its own only when it is sure, and gives a person the tools to fix the rest.

## Language

**Subtitle**:
An external subtitle file next to a video (SRT, ASS/SSA or WebVTT), the thing the guard checks and may rewrite; it keeps its identity when the guard itself moves it.
_Avoid_: Sub, track (a track is inside the video)

**Version**:
One exact content of a subtitle file; any change on disk, by the guard or by anyone else, makes a new version.
_Avoid_: Revision, hash (how a version is recognised, not what it is)

**Reference track**:
A subtitle track embedded in the video, used as the timing truth for its subtitles.
_Avoid_: Embedded subtitle, internal sub

**Backup**:
A saved copy of an earlier version of a subtitle, made only when the guard is about to write, so any version can be restored.
_Avoid_: Snapshot, history copy

**Confidence**:
A 0 to 100 score of how sure the guard is that a subtitle is in sync after a check, with bands high, medium, low and none.
_Avoid_: Probability, accuracy

**Automatic fix**:
A rewrite of a subtitle's timing that the guard writes without a person, allowed only when the confidence is high.
_Avoid_: Auto-sync, correction

**Proposal**:
A fix the guard computed but did not write because its confidence is not high; it belongs to one exact version of the file, and a person applies it, adjusts it or discards it.
_Avoid_: Suggestion, draft, pending fix

**Part**:
A stretch of consecutive lines that a fix moves by the same amount; a proposal is made of parts, or of one speed correction.
_Avoid_: Segment, chunk, range

**Audio sync**:
Aligning a subtitle to the speech in the video's sound instead of to a reference track, used when the video has no reference track.
_Avoid_: ffsubsync (the tool, not the concept), audio fix

**Allowed folder**:
A folder the guard may read and write subtitles in; nothing outside the allowed folders is ever touched, and they are set only by the environment.
_Avoid_: Root, library (a library is what is watched)

**Watched folder**:
A media folder inside the allowed folders that the guard scans for new or changed subtitles when it runs without Bazarr.
_Avoid_: Library path, source

**Mode**:
Whether the guard writes automatic fixes ("Fix automatically") or only records what it would do ("Report only").
_Avoid_: Dry run, apply flag

**Wrong episode**:
A subtitle that lines up with a neighbouring episode instead of its own video; it is never moved, a replacement is asked for instead.
_Avoid_: Misplaced subtitle, belongs elsewhere

**Scan**:
A check of every subtitle of the library, through Bazarr or the watched folders, started by a person or on a schedule.
_Avoid_: Sync, refresh, full check

**Replacement**:
Another subtitle the guard asks Bazarr for, by blacklisting the current one, when a Bazarr download is not in sync with high confidence; replacements are tried before anything reaches Review.
_Avoid_: Retry, re-download

**Best version**:
The best of the subtitles tried for one video and language (the original and its replacements), kept when none of them reaches high confidence.
_Avoid_: Best effort, fallback

**Review**:
The queue of subtitles waiting for a person's decision: proposals, subtitles that cannot be fixed, and subtitles that belong to another episode; it is the home of the UI.
_Avoid_: Needs a look, inbox, to do, pending (pending is for queued jobs)
