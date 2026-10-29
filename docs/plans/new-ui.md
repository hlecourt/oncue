# Plan: engine safety rule, FastAPI API, React + shadcn/ui front

Merged from two independent plans (Claude, Codex), then challenged with the owner on 2026-10-29. Vocabulary: [CONTEXT.md](../../CONTEXT.md). Decisions: [ADR 0001 to 0005](../adr/).

## The need

1. Automatic fix only at high confidence (ADR 0001).
2. Manual editing when needed: listen, adjust, apply.

## Decisions

### Engine

- **Automatic fix rule.** A subtitle is written without a person only at confidence 80 or more, otherwise a proposal goes to Review. This covers audio sync too.
- **Audio sync confidence.** It is scored by the guard itself, from speech onsets, a null test and the agreement of two voice detectors. Automatic audio writes stay off until calibration shows no false positives.
- **Bazarr first.**
  - A Bazarr download below high confidence gets replacements first.
  - Only when they all fail does the best version, with its proposal, reach Review.
  - Subtitles that did not come from Bazarr go straight to Review.
- **Wrong episode.** The subtitle is never moved: a replacement is asked for. If none comes, it goes to Review with "Belongs to S01E03", and the person keeps it or deletes it. `move_to_episode` is removed.
- **Subtitle entity.** A subtitle is a stored entity with an opaque id, a version (content hash), a state, a proposal and a best version (ADR 0004). Every action still confines paths.
- **Proposal.**
  - A proposal belongs to one version of the file. Any change on disk discards it and triggers a new check.
  - Apply refuses a stale version.
- **Backups.**
  - A backup is made only when the guard writes.
  - Retention: the first version always, plus the last 5 versions and anything under 30 days. Both values are settings.
  - A version history with restore is shown per subtitle.

### Screens

- **Review is the home.**
  - A compact strip of counts at the top.
  - A filterable table with multi-select.
  - Bulk "Ask Bazarr for new versions", "Keep as is" and "Check again". No bulk apply.
  - Default sort: proposals closest to the threshold first.
  - After an action, the next item opens with the same filters.
- **One screen per subtitle.**
  - Diagnosis and the parts of the proposal at the top, the editor below.
  - The line-by-line comparison becomes a column of the editor's line table.
  - "Before / Proposal" toggle. Actions: Apply proposal, Adjust (shift tool on top of the proposal), Discard.
- **Editor.**
  - Lines and preview on top, then a full-width timeline panel (transport, waveform, zoom, legend), then a full-width "Move the lines" panel.
  - Phone order: preview, timeline, move, lines.
  - Selection, playing row and changed timing are distinct states.
  - Only cue blocks are draggable.
  - Waveform: wavesurfer.js from server-side peaks, over a cached light audio copy (ADR 0005), after a spike.
- **Other pages.**
  - History: every check, filters, tasks with retry.
  - Log: aligned mono columns, JSON, copy, export.
  - System: health, effective configuration with its sources, backup space and clean-up, "Scan library" with progress and stop, Bazarr connection test.
    It also checks Bazarr's own settings through its API and warns when:
    - Bazarr's subtitle synchronization is on (it writes before the guard, without a confidence check, and the downloaded original is lost);
    - a post-processing threshold is on (subtitles scored above it never reach the guard);
    - post-processing is off, or its command does not call the guard.
  - Settings (ADR 0003):
    - Environment variables win, and their fields show as locked.
    - Allowed folders and the confidence threshold are not editable.
    - Editable: Bazarr URL and API key, watched folders inside the allowed folders, mode, workers, watch interval, number of replacements, the audio automatic-write switch (locked until calibrated), backup retention, webhook, URL base, authentication and API key.
- **Style.**
  - Vercel-style dense layout with plain, action-first words. The jargon goes under "Technical details" and the Log.
  - The copy is produced server-side next to the engine's decisions.
  - shadcn sidebar (icon-collapsible), breadcrumb, Cmd+K command menu, light/dark/system theme, Sonner toasts, Geist fonts.
  - Every screen has skeleton, empty, filtered-empty, error and retry, and disabled-with-reason states.
  - 44 px touch targets on phones. axe runs in the e2e tests.
- **First run.** The first scan runs in "Report only" until the person switches the mode to "Fix automatically".
- **Notifications.**
  - The Review count shows in the browser tab and the sidebar.
  - An optional webhook digest when subtitles reach Review, at most one message per interval.

### Access and platform

- **Authentication, like the *arr apps.**
  - One account with form login, required by default.
  - Optional "Disabled for local addresses" (off by default, with a warning).
  - "External" for a reverse proxy that authenticates (with a warning).
  - An API key, sent as the `X-Api-Key` header, for scans, Review state and checking a subtitle.
- **Platform.**
  - Python 3.11 or later.
  - The last two versions of Chrome, Firefox and Safari, on macOS and iOS/iPadOS.
  - A URL base setting, applied on restart, as in Sonarr/Radarr/Bazarr.

## Architecture

- **API.**
  - FastAPI + Pydantic 2 + uvicorn, in a `web` extra (ADR 0002).
  - The engine, CLI and Bazarr hook stay numpy only, with lazy web imports and a CI job.
  - JSON REST under `{url base}/api`, OpenAPI committed, TypeScript types from openapi-typescript + openapi-fetch, drift check in CI.
  - Session cookie (HttpOnly, SameSite=Strict, Secure behind HTTPS) with the CSRF token in a header. Origin and Host checks, JSON content type on writes, body and concurrency limits. Proxy headers are ignored unless a proxy is configured.
  - Polling for live state in v1.
- **Front.**
  - Vite + React 19 + TypeScript.
  - Tailwind 4 and shadcn/ui (Radix, vendored).
  - TanStack Router, Query, Table and Virtual.
  - Built into the wheel, served by the API process: API routes first, hashed assets immutable, SPA fallback for HTML only.
  - CSP without inline scripts. No Next.js and no Node at runtime.
- **Docker.** Multi-stage, with Node in the build stage only. pnpm lockfile, exact pins, Dependabot for npm.

## Phases (each ends on a check)

1. **Engine on the current UI.**
   - The automatic fix rule and proposals, the Subtitle entity and proposal versions, Bazarr first.
   - Wrong episode becomes a replacement, `move_to_episode` is removed.
   - Backups only on write, plus retention.
   - Exit: no fixture below confidence 80 is ever rewritten, a stale proposal cannot be applied, the torture benchmark documents every decision change, and all tests pass.
2. **Audio confidence and calibration.**
   - Speech-onset scoring, the two-detector agreement, and a calibration set of films with known offsets.
   - Exit: a calibration report. The automatic audio write stays locked unless the report shows no false positive.
3. **API alongside the current server.**
   - Every endpoint, settings storage with env precedence, auth modes, API key, webhook, scans with progress, versions and restore, the cached audio copy and peaks job.
   - Exit: API tests for every endpoint, the security tests ported, OpenAPI committed, the hook green in a numpy-only environment, and the Bazarr setup check tested against a stubbed Bazarr API for each of its warnings.
4. **Waveform spike, then the React front page by page.**
   - Order: Review, subtitle screen with the editor, History, Log, System, Settings, Login.
   - Exit: Playwright e2e on fixture media (Chromium and WebKit) for every action, conflict and expiry. Zero serious axe violations, zero CSP violations, bundle budget met (about 200 KB gzip on first load, editor lazy).
5. **Cutover in one change.**
   - The React front becomes the UI, the old server and templates are deleted, the docs and screenshots are updated.
   - Exit: a clean wheel and both Docker architectures serve the full UI, and a release dry run is green.

## Later

Bulk apply (never below the threshold), SSE, charts, undo/redo, two-point linear correction, text editing, CPS indicators, Apprise, multi-user, a whole-library waveform overview cache beyond the editor's.
