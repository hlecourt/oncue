# subtitle-guard

Verify and repair the timing of downloaded subtitles against a subtitle track embedded in the video, the one source of timing that is authored for that exact file.

Status: alpha. It runs on one home server as a [Bazarr](https://www.bazarr.media/) post-processing hook. A standalone mode, a job queue and a web UI are planned (see Roadmap).

## Why

Subtitles found online are often made for another release: a streaming cut instead of the Blu-ray, a 25 fps PAL master instead of 23.976, a TV recording with ad breaks. Audio-based tools such as ffsubsync apply a single offset and framerate, which cannot fix a different cut, and they sometimes report success on a wrong result. subtitle-guard instead:

- reads the timing of a subtitle track already embedded in the video (text or image, any language, forced and commentary tracks excluded) from the Matroska Cues index in well under a second, with a full read as fallback;
- realigns the downloaded subtitle piecewise with an exact dynamic programming search over offsets (0.05 s steps, ±400 s), using line starts and ends, after picking the framerate ratio;
- accepts the result only if it clearly beats the same search run against a time-mirrored reference (a null that has the same density but no relation to the subtitle), and only if every part of the result is supported by the reference;
- otherwise leaves the file untouched, flags a subtitle that matches a neighbouring episode, or asks Bazarr for another subtitle.

Every write is preceded by a backup and is atomic. Commands are dry runs unless `--apply` is given.

## Usage

```
subtitle-guard check VIDEO SUBTITLE [--apply]
subtitle-guard scan-series SERIES_ID [--apply]
subtitle-guard scan-movies [--apply]
subtitle-guard scan-all [--apply]
subtitle-guard needs-human
subtitle-guard hook VIDEO SUBTITLE LANGUAGE PROVIDER SUBTITLE_ID MEDIA_ID [SERIES_ID]
```

As a Bazarr post-processing command (Settings > Subtitles > Post-processing), with the package available to Bazarr's Python:

```
python -m subtitle_guard hook "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{subtitle_id}}" "{{episode_id}}" "{{series_id}}"
```

Turn off Bazarr's own audio sync (`use_subsync`) when the guard is in place: the guard falls back to ffsubsync by itself for videos without any embedded subtitle track, and marks those results as not verified.

Settings come from the environment:

| Variable | Default |
|---|---|
| `SUBTITLE_GUARD_STATE` | `/config/subtitle-guard` |
| `SUBTITLE_GUARD_BACKUPS` | `/data/backup/subtitle-guard` |
| `SUBTITLE_GUARD_FFSUBSYNC_PATH` | `/app/bazarr/bin/libs` |
| `BAZARR_URL` | `http://localhost:6767` |
| `BAZARR_API_KEY` | read from `BAZARR_CONFIG` |
| `BAZARR_CONFIG` | `/config/config/config.yaml` |

## Decisions

| Decision | Meaning |
|---|---|
| `kept` | already in sync, nothing written |
| `fixed` | realigned and verified, original backed up |
| `rejected` | could not be realigned with confidence; in hook mode Bazarr is asked for another subtitle |
| `wrong-episode` | the subtitle matches a neighbouring episode; reported, not blacklisted |
| `audio-synced` | no embedded track; synced to the audio with ffsubsync, not verified |
| `unverified` | nothing to compare with, or too few lines (forced subtitles) |

`needs-human` lists every subtitle whose last decision is not settled, or that still has weak minutes.

## Accuracy

`benchmarks/torture.py` misaligns known-good tracks in 42 ways and measures every line against its true time. On 12 tracks of 420 to 720 lines (French, Japanese and German, one dialogue-heavy series), 502 of 504 runs end as expected, with 97 to 100% of lines within one second when fixed, including different cuts, ad breaks, framerate and speed errors up to 5%, sloppy timing, merged or split lines, and dense stretches. Wrong-episode subtitles and offsets beyond the search range are rejected. The two misses are fragmented dense stretches that are realigned correctly but rejected as a precaution. `benchmarks/tears_of_steel.py` builds a public corpus from the Blender open movie Tears of Steel.

## Limits

- Cumulative offsets beyond ±400 s (long TV recordings with many ad breaks) are rejected, not fixed.
- Lines of a scene that exists in the subtitle but not in the video are placed as well as possible, not removed.
- Without any embedded subtitle track, timing cannot be verified; the ffsubsync fallback cannot fix a different cut.

## Roadmap

1. Package, tests, Docker image (this release).
2. Job queue, worker, structured logs and a confidence score.
3. Standalone mode that scans media folders without Bazarr.
4. Web UI: review queue, side-by-side reading, manual adjustment, retry.

## Development

```
uv venv --python 3.12 && uv pip install -e ".[test]"
.venv/bin/python -m pytest
```

## License

GPL-3.0-or-later. See `LICENSE`.
