# Contributing

Thanks for helping. Bug reports, real-world subtitles that OnCue gets wrong, and pull requests are all welcome.

## Report a bug

Open an issue with the bug template. The most useful thing is the line of the technical log for the subtitle
(`oncue log 200 | grep <file name>`, or the Log page of the UI) and, if you can share them, the subtitle and the
output of `ffprobe -show_streams` for the video.

Security issues go through [SECURITY.md](SECURITY.md), not public issues.

## Set up

```
git clone https://github.com/hlecourt/oncue
cd oncue
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

You need `ffmpeg` and `ffprobe` on the PATH for the tests that build small videos.

The web UI lives in `frontend/` (Vite, React, TypeScript, Tailwind and shadcn/ui). It needs Node 24 and the pnpm version pinned
in `package.json`, through corepack:

```
cd frontend
corepack pnpm install
corepack pnpm run build        # writes into src/oncue/api/static, which the API serves
```

To work on it, run `oncue ui` (port 8484) and `corepack pnpm run dev` side by side: Vite serves the front on port 5173
and passes `/api` to the API (`ONCUE_API` points it elsewhere). After a change to the API, regenerate the contract and
the types with `python -m oncue.api.contract` then `corepack pnpm run gen:api`.

## Before you open a pull request

```
.venv/bin/python -m pytest -q --cov=oncue
.venv/bin/ruff check src tests benchmarks
cd frontend && corepack pnpm run typecheck && corepack pnpm run test && corepack pnpm run e2e
```

`pnpm run e2e` builds nothing: build the front first. It starts the API on fixture media and runs the journeys in Chromium and
WebKit (`corepack pnpm exec playwright install chromium webkit` once), with axe on every page.

CI runs the Python tests on 3.11 to 3.13, checks that the Bazarr hook runs with numpy only, runs the front checks and the
browser tests, builds the wheel with the UI in it and the Docker image, and runs CodeQL.

## Test policy

Every change that alters behaviour comes with a test that fails without it. A fix for a misaligned subtitle comes with a
synthetic case in `tests/` (see `tests/media.py` and `tests/synthetic.py`), and when it changes alignment decisions, with a run of
`benchmarks/torture.py` on the public Tears of Steel corpus (`benchmarks/tears_of_steel.py`), whose summary goes in the pull request.

## Style

- Plain Python 3.11+, standard library first. numpy is the only dependency of the engine, the CLI and the Bazarr hook; FastAPI and uvicorn come with the `web` extra and only the API imports them.
- Descriptive names without abbreviations, early returns, small functions that read top-down, no comments that repeat the code.
- Typed errors from `errors.py` for expected failures; never swallow an exception.
- UI copy in plain English for people who are not technical; the Log page is the technical view.
- In the front, controls have two heights: the default (36 px) for page actions, forms and tabs, `sm` (32 px) for filters, table
  toolbars and the editor. One filled button per screen, for the action that writes; the tone dot says what a row needs, the
  confidence badge stays neutral.
- In the front, the shadcn/ui components in `src/components/ui` are vendored: change them there. JSON goes through the typed
  client in `src/lib/api`; media (audio, peaks, frames) through its `mediaUrl`, so the URL base applies.
- Every change to a subtitle file goes through `files.py`.

## Translations

The UI speaks English, French and Simplified Chinese. The English text is the key: screens call `t("...")` in the front and
`tr("...")` in the API, and each language has one JSON catalog per side, `frontend/src/locales/<code>.json` and
`src/oncue/api/locales/<code>.json`, mapping the English text to its translation. A text missing from a catalog shows
in English.

- To fix or complete a language, edit its two catalogs. Keep every `{placeholder}` of the English text, and plain punctuation:
  no long dashes, curly quotes or ellipsis characters (in Chinese, quote with corner brackets).
- To add a language, add its code to `LOCALES` in `frontend/src/lib/i18n.ts` (with its name in that language and its BCP 47
  tag) and in `src/oncue/api/i18n.py`, import its catalog in `i18n.ts`, and create both catalogs from the French ones.
- Plurals are separate keys (`"{count} check"`, `"{count} checks"`); write the plural form your language needs for each.
- `pnpm run test` and `pytest tests/test_api_i18n.py` check that every translation keeps its placeholders and that the
  catalogs of a side have the same keys.
- The technical log, the CLI and the error details of the core modules stay in English.

## Licence

OnCue is GPL-3.0-or-later. By submitting a contribution you agree that it is licensed under the same terms.
There is no CLA.

## Releases

Maintainers update `CHANGELOG.md` and the version in `src/oncue/__init__.py`, then push a `vX.Y.Z` tag. The release
workflow publishes the multi-arch image to `ghcr.io/hlecourt/oncue` with provenance and an SBOM, and attaches the wheel
and the sdist to the GitHub release. Versions follow [Semantic Versioning](https://semver.org); before 1.0, minor versions may
change behaviour.
