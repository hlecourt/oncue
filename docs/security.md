# Security model

How subtitle-guard protects its web UI, your files and Bazarr's key. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

- On loopback the UI needs no login. Anywhere else it needs a token: `SUBTITLE_GUARD_UI_TOKEN`, or one generated at first start,
  stored in the state directory with mode 600 and printed in the logs. Logging in opens a session cookie (HttpOnly,
  SameSite=Strict, Secure behind an HTTPS proxy that sets `X-Forwarded-Proto`).
- Every action is a POST with a CSRF token, from the same origin; without a token only loopback host names are accepted. Pages
  send a strict Content-Security-Policy and refuse to be framed. Requests are size- and time-limited.
- The UI only acts on subtitles the guard already checked, never through a symlink, and only under `SUBTITLE_GUARD_ROOTS` (or the
  watched folders) when set.
