# Changelog

From 0.1.0 on, the notes of each release are on the [releases page](https://github.com/hlecourt/oncue/releases), generated
from the pull requests it merged. The project uses [Semantic Versioning](https://semver.org); below is the first version.

## [0.1.0] - 2026-10-30

First public version.

### Added

- Automatic fixes only at high confidence (80 or more). Any other fix, including every audio sync for now, is stored as a
  proposal for the exact version of the file, shown for review, and applied by a person; applying refuses a file that changed.
- Bazarr first: a download that is not trusted (a proposal, a rejection, a wrong episode, or in sync with low confidence) asks
  Bazarr for another subtitle before anything reaches review; copies of the tries are kept outside the backups until the best
  version is chosen.
- Backups only when something is written, with retention (first version, last 5, last 30 days), and a delete action for a
  subtitle that belongs to another episode, which is no longer moved.

- Web UI built with React and shadcn/ui, served by a FastAPI API from the same process (the `web` extra): Review as the home
  page with bulk keep, check again and ask Bazarr for another; one screen per subtitle with its decision, the fix found and
  the editor; History, Log, System (health, Bazarr setup check, library scan, backups) and Settings. Light and dark themes,
  a `Ctrl K` search, phone layouts.
- Listen and edit, laid out like a video editor: the video, the lines with the video's own line under each, and a docked
  timeline with your subtitle, the sound and the video's subtitle as separate tracks over a cached light copy of the sound.
  Anchor a line where its voice starts, or nudge by 0.1 s and 1 s, on every line, from a line on, or on a selection; changes
  add up into a list with OnCue's fix (which can be turned off), undo with Ctrl Z, and one save writes them all with a
  backup and a check that the file did not change. Full keyboard control, phone layout.
- The editor plays the real video with the sound, in short windows made on demand around the playhead: a picture in H.264 is
  copied as is, so it shows as soon as the editor opens; any other one is encoded again in 720p, 10 s at a time, with a
  loading sign while it is made. The next window is made while the current one plays, so playback never stops at a seam.
- Settings in the UI, stored next to the state; an environment variable always wins and the allowed folders and the
  confidence threshold stay set by the environment. A URL base for reverse proxies, login modes like the *arr apps (always,
  not from private addresses, or a proxy that authenticates), an API key for scripts, a webhook digest of what reached
  Review, and library scans started from the UI.

- Check of external SRT, WebVTT and ASS/SSA subtitles against a text or bitmap subtitle track embedded in the video, read from
  the Matroska Cues index when possible, with a piecewise-constant alignment (offsets, ad breaks, cuts, framerate and speed
  errors), a null test against a mirrored reference, segment support and per-minute checks.
- Repair only when the result can be trusted, with a confidence score, backups and restore; audio sync with ffsubsync, marked
  as unverified, when the video has no subtitle track.
- Detection of subtitles that belong to a neighbouring episode, and of wrong end times.
- Bazarr post-processing hook with blacklist and retry of rejected subtitles, library scans through the Bazarr API, and a
  standalone mode that watches media folders.
- SQLite job queue with parallel workers, supervision, crash recovery, heartbeats and `/ready`.
- Global JSON-lines log with component.past-tense events, startup and health reports.
- Docker image for amd64 and arm64 running as a non-root user, with pinned bases, a hashed dependency lock and a health check;
  the web UI is built in a separate stage, so Node is never in the image.
- A Dashboard of what OnCue did over a period, with subtitle providers ranked by the verdict on arrival, the share of sure
  fixes against those sent to Review, and charts readable with the keyboard and screen readers; a Subtitles page
  listing every checked subtitle, each opening in the editor; the editor's History tab and a menu to switch to another
  subtitle of the same video.
- The UI in English, French and Simplified Chinese, from the browser's language or the menu, API sentences included.
- In the editor, lines placed one by one are held and later moves of every line go around them; leaving with unsaved changes
  asks to save, discard or stay; the sound is drawn by wavesurfer.js; Enter picks a line on the timeline, and phones get the
  sound track, speed and zoom in the menu.
- In `report` mode, Review says that nothing is written. Forced subtitles stay out of Review. Pages that fail to load say so
  and offer to try again.

### Fixed

- Two instances on one machine no longer log each other out: each names its session cookie after an id kept in its own
  state folder, so two containers that both listen on 8484 get two cookies.
- Check again wrote fixes in `report` mode and without allowed folders; it now follows the mode like every other check.
- A finished library scan, or one whose listing failed, kept new scans from starting.
- The lines shown in the editor and the version they are saved against now come from the same read of the file, and a delete
  checks the version under the subtitle's lock.
- The Dashboard and the audio preparation no longer re-read every check, every file or the video's tracks on each refresh.
- The Matroska Cues index is read again: a CRC-32 or padding element in the seek head made every read fall back to the slower
  packet scan. A second seek head at the end of the file is followed too.
- Lowering the number of workers requeues the checks they were running and updates the worker list, so `/ready` no longer
  stays at 503. A failed sound copy says so in the editor with Try again instead of retrying forever.
- Bazarr replacements: an old timer no longer clears a newer request, restoring the best attempt is skipped when the file
  changed meanwhile, and a save from the editor is always recorded in the history, even when its measure fails.
- An allowed folder can itself be a watched folder. The Bazarr hook refuses extra arguments instead of misreading them.
- The editor keeps your changes when the file changes on disk and says so, instead of reloading; long French labels wrap
  on History, the Dashboard, the System page and the editor on phones. The unused `/apply` and `/shift` API routes are
  removed: `/retime` does both.
- The editor's preview asks for one video frame at a time: the server extracts one at a time and refused the others, which
  showed on slower machines.

### Security

- Web UI login with a user name and a password (scrypt hash, generated on first start beyond loopback, `set-password` to change
  it), expiring server-side sessions, a lockout after failed logins, CSRF tokens bound to the session and sent in a header,
  origin and host checks, a CSP with no inline script and strict security headers, request size and time limits.
- Login modes that cannot be fooled by a proxy or a rebound DNS name: `local` needs a private client opening the UI by an IP or
  a local name, `external` trusts only `ONCUE_TRUSTED_PROXIES`, and the lockout counts the real client behind a
  trusted proxy. CSRF tokens are always held by the server, bodies need a `Content-Length`, the UI changes files only under
  `ONCUE_ROOTS` and through folders opened without following links, and a new Bazarr address needs its key again.
- Every subtitle read and write is confined to `ONCUE_ROOTS`, never follows a symlink and only touches regular files.
- Nothing is written anywhere, from the UI, the hook, the scans or the CLI, until `ONCUE_ROOTS` is set, and reads go
  through folders opened without following links, like writes.
- Bazarr's API key can no longer be redirected: it cannot be emptied while the address comes from Settings, and Bazarr's
  `config.yaml` is only read for the default address. Calls to Bazarr and the webhook refuse link-local and metadata addresses,
  and a failed Bazarr check no longer echoes the error.
- ffmpeg and ffprobe only open local files in supported containers, and ffsubsync only runs on a file they accepted.
- A password change ends every session and replaces the API key; failed logins also lock the account; the `Secure` cookie flag
  follows `X-Forwarded-Proto` only from a trusted proxy; IPv4-mapped addresses are judged by their IPv4 address; the API
  description needs a login.
- Videos are opened once through folders that refuse links below the allowed folders, and ffprobe, ffmpeg and the Matroska
  reader work on that open file, so a folder swapped for a link after the check cannot redirect them; ffsubsync only gets a
  sound file and a subtitle copy made by OnCue.
- With a login, the API key is only shown or replaced after the password is entered again. The account lock never applies to
  an address that logged in successfully in the last 30 days, and a request body must arrive within 30 seconds.
- Hostile files can no longer stall OnCue: subtitle markup is stripped in linear time, a FIFO named like a subtitle is
  refused instead of blocking a worker, and the Matroska reader ignores repeated index entries and caps what it reads.
- Changing the login mode or the URL base asks for the password, and the URL base only accepts plain path segments.
  Leftover temporary files are only cleaned inside the allowed folders, and OnCue's sound work folder is emptied at
  start.
