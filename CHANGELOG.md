# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com),
and the project uses [Semantic Versioning](https://semver.org).

## [Unreleased]

## [0.1.0] - unreleased

First public version.

### Added

- Check of external SRT, WebVTT and ASS/SSA subtitles against a text or bitmap subtitle track embedded in the video, read from
  the Matroska Cues index when possible, with a piecewise-constant alignment (offsets, ad breaks, cuts, framerate and speed
  errors), a null test against a mirrored reference, segment support and per-minute checks.
- Repair only when the result can be trusted, with a confidence score, backups and restore; audio sync with ffsubsync, marked
  as unverified, when the video has no subtitle track.
- Detection of subtitles that belong to a neighbouring episode, and of wrong end times.
- Bazarr post-processing hook with blacklist and retry of rejected subtitles, library scans through the Bazarr API, and a
  standalone mode that watches media folders.
- SQLite job queue with parallel workers, supervision, crash recovery, heartbeats and `/ready`.
- Web UI in plain words: overview, the subtitles that need a look with one-click fixes, a page per subtitle with the wrong parts,
  a timeline and a line-by-line view, history of every check with what changed, and a technical log with copyable metadata.
- Global JSON-lines log with component.past-tense events, startup and health reports.
- Docker image running as a non-root user, with pinned bases, a hashed dependency lock and a health check.

### Security

- Web UI token required beyond loopback (generated when unset), sessions, CSRF tokens, origin and host checks, strict security
  headers, request limits, and actions limited to known subtitles inside the configured roots and never through symlinks.
