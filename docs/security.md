# Security model

How subtitle-guard protects its web UI, your files and Bazarr's key. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

- Listening on loopback, the UI needs no login, and it then only answers requests made from the same machine to a loopback name,
  without proxy headers. A reverse proxy in front of it must forward `X-Forwarded-For`, or set a password. Anywhere else it
  needs a user name and password: `SUBTITLE_GUARD_UI_USER` and
  `SUBTITLE_GUARD_UI_PASSWORD`, or an account set with `subtitle-guard set-password` (stored as a scrypt hash with mode 600), or on
  first start without either, a password for `admin` generated and printed once. A broken account file stops the UI instead of
  opening it. Five wrong attempts lock a client out for 10 minutes, and twenty in 10 minutes from any address lock the account,
  except for addresses that logged in successfully in the last 30 days, so a stranger cannot lock you out. Logging in opens
  a session that expires on the server after 30 days (HttpOnly, SameSite=Strict, Secure behind a trusted HTTPS proxy that sets
  `X-Forwarded-Proto`); logging out ends it, and changing the password ends every session and replaces the API key.
- Login modes (`SUBTITLE_GUARD_AUTH`): `local` skips the login only for a client with a private address that opens the UI by an IP
  address or a local name (`nas`, `nas.local`, `.lan`, `.home.arpa`, `.internal`), so a public name pointed at your network
  gets the login. `external` skips it only for requests from `SUBTITLE_GUARD_TRUSTED_PROXIES`; anyone else gets the login. Proxy
  headers are ignored unless they come from a trusted proxy, whose `X-Forwarded-For` then names the client for these rules and
  for the lockout. In `local` mode anyone on your network has full access, API key included: keep the default `forms` unless
  you trust every device on it.
- Every change is a JSON request with a `Content-Length`, carrying a CSRF token held by the server (the session's, or one kept
  for clients that need no login) in a header, or the API key, from the same origin. Pages send a strict
  Content-Security-Policy with no inline script and refuse to be framed. Requests are size-limited and a request body must arrive
  within 30 seconds. The UI does not limit how long the headers take to arrive: a reverse proxy in front of it does that.
- The UI only acts on subtitles the guard already checked, and reads and writes them through folders opened without following
  symlinks at any level of the path. Nothing is written, by the UI, the hook, the scans or the CLI, unless
  `SUBTITLE_GUARD_ROOTS` (or `SUBTITLE_GUARD_LIBRARY`) is set, and only under those folders; videos outside them are never read.
- ffmpeg and ffprobe only read local files in the containers the guard supports (Matroska, MP4, AVI, MPEG-TS), so a playlist
  disguised as a video cannot make them fetch anything else; ffsubsync only runs on a file they accepted.
- Bazarr's API key is only sent to the address it was entered for: changing the address asks for the key again, and the key
  cannot be emptied while the address comes from Settings (Bazarr's own `config.yaml` is only read for the default address).
  Calls to Bazarr and the webhook refuse link-local and cloud metadata addresses. The API description at `/api/openapi.json`
  needs a login like the rest of the API.
