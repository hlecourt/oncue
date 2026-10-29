# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com),
and the project uses [Semantic Versioning](https://semver.org).

## [Unreleased]

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
  add up into a list with the guard's fix (which can be turned off), undo with Ctrl Z, and one save writes them all with a
  backup and a check that the file did not change. Full keyboard control, phone layout.
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

### Security

- Web UI login with a user name and a password (scrypt hash, generated on first start beyond loopback, `set-password` to change
  it), expiring server-side sessions, a lockout after failed logins, CSRF tokens bound to the session and sent in a header,
  origin and host checks, a CSP with no inline script and strict security headers, request size and time limits.
- Login modes that cannot be fooled by a proxy or a rebound DNS name: `local` needs a private client opening the UI by an IP or
  a local name, `external` trusts only `SUBTITLE_GUARD_TRUSTED_PROXIES`, and the lockout counts the real client behind a
  trusted proxy. CSRF tokens are always held by the server, bodies need a `Content-Length`, the UI changes files only under
  `SUBTITLE_GUARD_ROOTS` and through folders opened without following links, and a new Bazarr address needs its key again.
- Every subtitle read and write is confined to `SUBTITLE_GUARD_ROOTS`, never follows a symlink and only touches regular files.
