# Security model

How subtitle-guard protects its web UI, your files and Bazarr's key. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

- Listening on loopback, the UI needs no login, and it then only answers requests made from the same machine to a loopback name,
  without proxy headers. A reverse proxy in front of it must forward `X-Forwarded-For`, or set a password. Anywhere else it
  needs a user name and password: `SUBTITLE_GUARD_UI_USER` and
  `SUBTITLE_GUARD_UI_PASSWORD`, or an account set with `subtitle-guard set-password` (stored as a scrypt hash with mode 600), or on
  first start without either, a password for `admin` generated and printed once. A broken account file stops the UI instead of
  opening it. Five wrong attempts lock a client out for 10 minutes. Logging in opens a session that expires on the server after
  30 days (HttpOnly, SameSite=Strict, Secure behind an HTTPS proxy that sets `X-Forwarded-Proto`); logging out ends it.
- Login modes (`SUBTITLE_GUARD_AUTH`): `local` skips the login only for a client with a private address that opens the UI by an IP
  address or a local name (`nas`, `nas.local`, `.lan`, `.home.arpa`, `.internal`), so a public name pointed at your network
  gets the login. `external` skips it only for requests from `SUBTITLE_GUARD_TRUSTED_PROXIES`; anyone else gets the login. Proxy
  headers are ignored unless they come from a trusted proxy, whose `X-Forwarded-For` then names the client for these rules and
  for the lockout.
- Every change is a JSON request with a `Content-Length`, carrying a CSRF token held by the server (the session's, or one kept
  for clients that need no login) in a header, or the API key, from the same origin. Pages send a strict
  Content-Security-Policy with no inline script and refuse to be framed. Requests are size- and time-limited.
- The UI only acts on subtitles the guard already checked, never through a symlink at any level of the path, and changes files
  only when `SUBTITLE_GUARD_ROOTS` is set, under those folders. Changing Bazarr's address asks for its API key again, so the key
  is never sent to a new address.
