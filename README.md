# subtitle-guard

[![licence: GPL-3.0-or-later](https://img.shields.io/badge/licence-GPL--3.0--or--later-blue.svg)](LICENSE)

Out-of-sync subtitles, fixed for you. subtitle-guard checks every external subtitle in your media library against the subtitle
track embedded in the video, fixes the timing on its own when it is sure, and lists the rest for you in a web UI. It works on
the folders of a Jellyfin, Plex or Emby library, and can also plug into Bazarr.

Status: alpha.

## Why

Subtitles found online are often made for another release: a streaming cut instead of the Blu-ray, a 25 fps master instead of
23.976, a TV recording with ad breaks. Audio-based tools such as ffsubsync mostly apply one offset and one frame rate, and
automatic syncs rarely tell you when they got it wrong. subtitle-guard instead:

- compares your subtitle with a track already embedded in the video (text or picture, any language), which is the one
  source of timing made for that exact file;
- realigns it part by part, so ad breaks, cuts and frame rate errors are fixed, not just a global offset;
- only trusts a fix that clearly beats the same search against a meaningless reference, and scores it from 0 to 100;
- anything it cannot fix with confidence is left as it is and listed for you in the web UI;
- changes only the timings, after a backup.

How it decides, step by step: [docs/how-it-works.md](docs/how-it-works.md).

## Quick start

```
git clone https://github.com/hlecourt/subtitle-guard.git
cd subtitle-guard/examples
docker compose up -d
```

Edit the media paths in the file first: `SUBTITLE_GUARD_LIBRARY` lists the folders to watch. Then open `http://<host>:8484`
with the token you set in `SUBTITLE_GUARD_UI_TOKEN`.

The guard checks every external subtitle it finds (`Movie.fr.srt`, `Show.S01E02.en.hi.ass`) and every new or changed one after
that. By default it only reports what it would do: add `--apply` to the `serve` command to let it write the fixes it is sure
about.

It needs Python 3.11+, `ffmpeg` and `ffprobe` on a Linux or macOS machine; see [docs/configuration.md](docs/configuration.md).

## With Bazarr (optional)

With [Bazarr](https://www.bazarr.media/), the guard checks each subtitle as soon as it is downloaded, and a subtitle it cannot
fix is blacklisted so that Bazarr fetches another one. Use
[examples/docker-compose.bazarr.yml](examples/docker-compose.bazarr.yml), then in Bazarr, Settings > Subtitles >
Post-processing, turn post-processing on with this command:

```
python -m subtitle_guard hook "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{subtitle_id}}" "{{episode_id}}" "{{series_id}}"
```

And turn off two Bazarr options, or the guard never sees the downloads as they arrive:

- **Subtitle synchronization** (`use_subsync`): it rewrites each file before the guard sees it, without any confidence check.
- **Post-processing threshold** (series and movies): it skips the best-scored downloads, and that score rates release names,
  not timing.

To check it works, download a subtitle from Bazarr: its log shows the hook's output.

## The web UI

`serve` also opens a web UI on port 8484 that lists what the guard was not sure about, with Keep, Check again and Ask Bazarr
for another.

## Safety

- `check`, the scans and the folder watch only report until `--apply` is given; the Bazarr hook writes the fixes it is sure
  about.
- Every write is atomic, after a backup.
- Beyond the machine itself, the web UI asks for a token.

## Settings

Settings are environment variables. The ones you are most likely to need:

| Variable | Default |
|---|---|
| `SUBTITLE_GUARD_LIBRARY` | unset: the media folders to watch |
| `SUBTITLE_GUARD_WORKERS` | `2` |
| `SUBTITLE_GUARD_UI_TOKEN` | unset: required beyond the machine itself |
| `BAZARR_URL` / `BAZARR_API_KEY` | `http://localhost:6767` / read from Bazarr's config |

All of them and the CLI: [docs/configuration.md](docs/configuration.md).

## Accuracy

`benchmarks/torture.py` misaligns known-good tracks in 42 ways and measures every line against its true time. On a private
corpus of 12 tracks (French, Japanese and German), 502 of 504 runs ended as expected, with 97 to 100% of lines within one
second when fixed; wrong-episode subtitles and random timings were rejected. A public corpus can be built from the Blender
open movie Tears of Steel:

```
python benchmarks/tears_of_steel.py /tmp/tos
python benchmarks/torture.py /tmp/tos/work /tmp/tos/corpus.json
```

## Limits

- Cumulative offsets beyond +/-400 s are rejected, not fixed.
- Lines of a scene missing from the video are placed as well as possible, not removed.
- Without any embedded subtitle track, timing cannot be verified, and the audio fallback cannot fix a different cut.

## Troubleshooting

- **A subtitle is never checked**: it must sit next to its video with the video's name (`Movie.mkv`, `Movie.fr.srt`) under a
  folder of `SUBTITLE_GUARD_LIBRARY`. With Bazarr, check that post-processing is on and that the command's paths exist inside
  the Bazarr container.

## Development

```
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
