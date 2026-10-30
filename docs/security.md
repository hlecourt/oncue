# How OnCue protects you

To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## Who can open the web UI

- **On the machine itself** (loopback), no login is needed, and the UI then only answers requests made from that machine to a
  loopback name, without proxy headers. A reverse proxy in front of it must forward `X-Forwarded-For`, or you set a password.
- **Anywhere else**, you need a user name and password: `ONCUE_UI_USER` and `ONCUE_UI_PASSWORD`, or an account set with
  `oncue set-password`. On a first start without either, a password for `admin` is generated and printed once in the logs. The
  password is stored as a scrypt hash in a file only OnCue can read, and a broken account file stops the UI instead of opening it.
- **Wrong passwords**: five in a row lock a client out for 10 minutes. Twenty in 10 minutes, from any address, lock the account
  for every address that has not logged in successfully in the last 30 days. Behind a reverse proxy missing from
  `ONCUE_TRUSTED_PROXIES`, every client has the proxy's address, so one client's mistakes lock out everyone.
- **Sessions** last 30 days on the server (HttpOnly, SameSite=Strict, and Secure behind a trusted HTTPS proxy). Logging out ends
  yours; changing the password ends every session and replaces the API key.
- **Login modes** (`ONCUE_AUTH`): `local` skips the login only for a client with a private address that opens the UI by an IP
  address or a local name (`nas`, `nas.local`, `.lan`, `.home.arpa`, `.internal`). Anyone on your network then has full access,
  API key included, so keep the default `forms` unless you trust every device on it. `external` skips the login only for
  requests coming from `ONCUE_TRUSTED_PROXIES`, for a reverse proxy that authenticates on its own.

## What every request must carry

- Every change is a JSON request from the same origin, with a CSRF token held by the server (or the API key) in a header.
- Pages run no inline script and refuse to be framed. Requests are size-limited, and a request body must arrive within 30
  seconds. The UI does not limit how long the headers take: a reverse proxy in front of it does that.
- The API description at `/api/openapi.json` needs a login like the rest of the API.

## What OnCue may touch

- Nothing is written, by the UI, the Bazarr hook, the scans or the CLI, unless `ONCUE_ROOTS` (or `ONCUE_LIBRARY`) is set, and
  only inside those folders. Videos outside them are never read.
- The UI only acts on subtitles OnCue already checked, through folders opened without following symbolic links anywhere in the
  path.
- A subtitle is rewritten atomically, one writer at a time, after a backup, and only if it did not change during the check.

## Files from the internet

Subtitles and videos downloaded from the internet are treated as hostile. They are parsed with size limits, ffprobe, ffmpeg and
ffsubsync run with time limits, and ffprobe and ffmpeg only read local files in the formats OnCue supports (Matroska, MP4, AVI,
MPEG-TS), so a playlist disguised as a video cannot make them fetch anything. Keep the ffmpeg of your image up to date.

## Bazarr's API key

The key is read from the environment or from Bazarr's config, only sent to the address it was entered for, never across a
redirect to another host, and never written to the log. Changing Bazarr's address asks for the key again. Calls to Bazarr and to
the webhook refuse link-local and cloud metadata addresses.
