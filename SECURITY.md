# Security policy

## Supported versions

Only the latest release gets security fixes. subtitle-guard is young (0.x): upgrade to the latest release before reporting.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through
[GitHub private vulnerability reporting](https://github.com/hlecourt/subtitle-guard/security/advisories/new),
or by email to hlecourt.pro@gmail.com if you cannot use GitHub.

Include the version, how you run it (Docker, Bazarr hook, standalone), what an attacker needs (network access to the UI, write
access to the media folder, a crafted subtitle or video) and the steps to reproduce.

You will get an answer within 7 days. A fix is released as soon as it is ready, and the report is published as a GitHub security
advisory once users had a reasonable time to upgrade, normally within 90 days. You are credited unless you ask not to be.

## Scope and what the project protects

- The web UI can change subtitle files. Anywhere but on loopback it requires a token (`SUBTITLE_GUARD_UI_TOKEN`, or one generated
  at first start), every POST needs a CSRF token and the same origin, and it only acts on subtitles it already checked, inside
  `SUBTITLE_GUARD_ROOTS` when set, never through a symlink.
- Subtitle files are rewritten atomically, one writer at a time, after a backup, and only when they did not change during the check.
- Untrusted inputs are subtitle files and videos downloaded from the internet. They are parsed with size limits, and ffprobe,
  ffmpeg and ffsubsync run with time limits and the file protocol only. Keep the ffmpeg in your image up to date.
- The Bazarr API key is read from the environment or Bazarr's config, sent only to `BAZARR_URL`, never across a redirect to
  another host, and never written to the log.
