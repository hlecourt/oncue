# Security policy

## Supported versions

Only the latest release gets security fixes. OnCue is young (0.x): upgrade to the latest release before reporting.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through
[GitHub private vulnerability reporting](https://github.com/hlecourt/oncue/security/advisories/new),
or by email to hlecourt.pro@gmail.com if you cannot use GitHub.

Include the version, how you run it (Docker, Bazarr hook, standalone), what an attacker needs (network access to the UI, write
access to the media folder, a crafted subtitle or video) and the steps to reproduce.

You will get an answer within 7 days. A fix is released as soon as it is ready, and the report is published as a GitHub security
advisory once users had a reasonable time to upgrade, normally within 90 days. You are credited unless you ask not to be.

## Scope and what the project protects

- The web UI can change subtitle files. Anywhere but on loopback it requires a user name and password (stored as a scrypt hash;
  generated and printed once when unset), and a broken account file stops the UI rather than opening it. Without a password it
  only serves loopback clients asking for a loopback name with no proxy headers. Proxy headers count only from
  `ONCUE_TRUSTED_PROXIES`. Every change needs a CSRF token held by the server in a header (or the API key) and the
  same origin, the pages run no inline script, and the UI only changes subtitles it already checked, inside
  `ONCUE_ROOTS`, which must be set.
- Subtitle files are rewritten atomically, one writer at a time, after a backup, and only when they did not change during the check.
- Untrusted inputs are subtitle files and videos downloaded from the internet. They are parsed with size limits, and ffprobe,
  ffmpeg and ffsubsync run with time limits, and ffprobe and ffmpeg read with the file protocol only. Keep the ffmpeg in your image up to date.
- The Bazarr API key is read from the environment or Bazarr's config, sent only to `BAZARR_URL`, never across a redirect to
  another host, and never written to the log.
