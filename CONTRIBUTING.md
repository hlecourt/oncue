# Contributing

Thanks for taking a look. The most useful contribution is a subtitle OnCue gets wrong: it becomes a test.

## Reporting a problem

Open an issue with the bug template. Add the log line for that subtitle (Log page, or `oncue log 200 | grep <file name>`)
and, if you can share them, the subtitle file and the output of `ffprobe -show_streams` for the video.

Found a security issue? Please report it privately: see [SECURITY.md](SECURITY.md).

## Running it from source

```
git clone https://github.com/hlecourt/oncue
cd oncue
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

You also need `ffmpeg` and `ffprobe`. The web UI lives in `frontend/` (React, TypeScript, Vite) and needs Node 24 with corepack:

```
cd frontend
corepack pnpm install
corepack pnpm run build
```

For live reload, run `oncue ui` (port 8484) and `corepack pnpm run dev` (port 5173) side by side. After changing the API,
run `python -m oncue.api.contract`, then `corepack pnpm run gen:api`.

## Before opening a pull request

```
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests benchmarks
cd frontend && corepack pnpm run typecheck && corepack pnpm run test && corepack pnpm run e2e
```

The browser tests need a built front and, once, `corepack pnpm exec playwright install chromium webkit`.

A few house rules:

- A change in behaviour comes with a test that fails without it. If it changes how subtitles get aligned, add the summary of
  `benchmarks/torture.py` on the Tears of Steel corpus (`benchmarks/tears_of_steel.py` builds it).
- Keep the code plain: descriptive names, early returns, no comments that repeat the code. numpy is the only dependency of the
  engine and of the Bazarr hook.
- Every write to a subtitle goes through `files.py`.
- UI text is plain English for people who are not technical; the Log page is the technical view.

## Translations

The UI speaks English, French and Simplified Chinese. The English text is the key, and each language has two catalogs:
`frontend/src/locales/<code>.json` for the UI and `src/oncue/api/locales/<code>.json` for the server.

- To fix a translation, edit those two files. Keep every `{placeholder}` and use plain punctuation (no long dashes or curly quotes).
- Plurals are separate keys (`"{count} check"`, `"{count} checks"`): write the form your language needs for each.
- To add a language, add it to `LOCALES` in `frontend/src/lib/i18n.ts` and in `src/oncue/api/i18n.py`, then start from a copy
  of the French catalogs.

`pnpm run test` and `pytest tests/test_api_i18n.py` catch missing keys and placeholders.

## Be kind

Assume good faith, keep the discussion about the code, and be patient with newcomers. Harassment gets you blocked.

## Licence

Contributions are licensed under GPL-3.0-or-later, like the rest of the project. There is no CLA.

## Releases

For maintainers, a release is a pull request: set the version in `src/oncue/__init__.py` and move the `Unreleased` entries of
`CHANGELOG.md` under a `## [X.Y.Z] - date` heading. Once it is merged, the release workflow publishes the image to
`ghcr.io/hlecourt/oncue` (and Docker Hub when its secrets are set), tags the merge `vX.Y.Z` and creates the GitHub release with
the wheel and that CHANGELOG section. Running the workflow by hand only rehearses it.
