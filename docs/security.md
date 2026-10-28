# Security model

How subtitle-guard protects its web UI, your files and Bazarr's key. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

- On loopback the UI needs no login. Anywhere else it needs a user name and password: `SUBTITLE_GUARD_UI_USER` and
  `SUBTITLE_GUARD_UI_PASSWORD`, or an account set with `subtitle-guard set-password` (stored as a scrypt hash with mode 600), or on
  first start without either, a password for `admin` generated and printed once. A broken account file stops the UI instead of
  opening it. Five wrong attempts lock a client out for 10 minutes. Logging in opens a session that expires on the server after
  30 days (HttpOnly, SameSite=Strict, Secure behind an HTTPS proxy that sets `X-Forwarded-Proto`); logging out ends it.
- Every action is a POST with a CSRF token, from the same origin; without a token only loopback host names are accepted. Pages
  send a strict Content-Security-Policy and refuse to be framed. Requests are size- and time-limited.
- The UI only acts on subtitles the guard already checked, never through a symlink, and only under `SUBTITLE_GUARD_ROOTS` (or the
  watched folders) when set.
