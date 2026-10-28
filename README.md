# subtitle-guard

Verify and repair the timing of downloaded subtitles against a subtitle track embedded in the video, the one source of timing that is authored for that exact file.

Status: alpha. It runs on one home server as a [Bazarr](https://www.bazarr.media/) post-processing hook, or on its own over media folders, with a job queue, parallel workers and a web UI for review.

## Why

Subtitles found online are often made for another release: a streaming cut instead of the Blu-ray, a 25 fps PAL master instead of 23.976, a TV recording with ad breaks. Audio-based tools such as ffsubsync apply a single offset and framerate, which cannot fix a different cut, and they sometimes report success on a wrong result. subtitle-guard instead:

- reads the timing of a subtitle track already embedded in the video (text or image, any language, forced and commentary tracks excluded) from the Matroska Cues index in well under a second, with a full read as fallback;
- realigns the downloaded subtitle piecewise with an exact dynamic programming search over offsets (0.05 s steps, ±400 s, then ±1200 s at 0.1 s steps when that fails), using line starts and ends, after picking the framerate ratio;
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
subtitle-guard scan-folder PATH [--apply] [--queue]
subtitle-guard watch PATH... [--apply]
subtitle-guard serve [--workers N] [--apply]
```

## Without Bazarr

`scan-folder` checks every video under a folder against its external subtitles (`Movie.fr.srt`, `Show.S01E02.en.hi.ass`...). Samples and hidden folders are skipped, and episode neighbours for the wrong-episode check come from `SxxEyy` or `NxNN` file names. `watch` queues every new or changed subtitle under one or more folders; `serve` does the same for the folders in `SUBTITLE_GUARD_LIBRARY`. Without Bazarr a rejected subtitle stays as it is and shows up for review; nothing is downloaded.

## Web UI

`subtitle-guard ui`, or `serve`, which also runs the workers, opens a web UI on port 8484 (`SUBTITLE_GUARD_UI_PORT`):

- a dashboard of jobs and confidence, refreshed automatically;
- the review queue: every subtitle whose last decision is not settled, has weak minutes or low confidence, until it is accepted or changes;
- one page per subtitle with a minute bar coloured by how many lines match, and a side-by-side reading of the embedded reference and the subtitle for the chosen minute;
- a manual shift: click a subtitle line, then the reference line it belongs to, set where the stretch ends, apply. The file is backed up first and measured again;
- one-click actions: accept as is, check again, restore the original provider file, ask Bazarr for another subtitle;
- every job with its error and a retry button, and JSON at `/api/status`, `/api/jobs` and `/api/review`.

The UI listens on 127.0.0.1 unless `SUBTITLE_GUARD_UI_HOST` says otherwise (the Docker image uses 0.0.0.0). It has no user accounts: set `SUBTITLE_GUARD_UI_TOKEN` and open it once with `?token=...` to protect it on a shared network. It only acts on subtitles the guard has already checked.

As a Bazarr post-processing command (Settings > Subtitles > Post-processing), with the package available to Bazarr's Python:

```
python -m subtitle_guard hook "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{subtitle_id}}" "{{episode_id}}" "{{series_id}}"
```

Bazarr runs this command without a shell. To keep its post-processing instant and let workers do the checks in parallel, queue the job instead:

```
/usr/bin/env PYTHONPATH=/opt/subtitle-guard /lsiopy/bin/python -m subtitle_guard hook --queue "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{subtitle_id}}" "{{episode_id}}" "{{series_id}}"
```

The hook needs a Python with numpy; on the linuxserver.io Bazarr image that is `/lsiopy/bin/python`.

Turn off Bazarr's own audio sync (`use_subsync`) when the guard is in place: the guard falls back to ffsubsync by itself for videos without any embedded subtitle track, and marks those results as not verified.

## Running as a service

`subtitle-guard worker` runs queued jobs with `SUBTITLE_GUARD_WORKERS` processes (2 by default). A supervisor restarts a worker that dies and requeues jobs left running after a crash. Alignment runs in parallel; full file reads and ffsubsync take turns through a lock so workers do not fight over a spinning disk. A rejected subtitle becomes a `blacklist` job, then a `retry-check` 15 minutes later that keeps the best version tried if Bazarr found nothing else. `examples/docker-compose.yml` shows the service next to Bazarr; both containers must mount the media and the state directory at the same paths.

```
subtitle-guard status              # jobs by status and confidence band
subtitle-guard jobs [failed]       # latest jobs
subtitle-guard job 42              # one job in full, with its error if any
subtitle-guard retry 42            # run a finished or failed job again
subtitle-guard scan-all --apply --queue   # check the whole library through the workers
```

Every decision and every job is logged as one JSON line in `subtitle-guard.log` (rotated at 10 MB) in the state directory, and on stdout for the worker, so `docker logs` shows them. Jobs live in `jobs.sqlite3` next to it.

Settings come from the environment:

| Variable | Default |
|---|---|
| `SUBTITLE_GUARD_STATE` | `/config/subtitle-guard` |
| `SUBTITLE_GUARD_BACKUPS` | `/data/backup/subtitle-guard` |
| `SUBTITLE_GUARD_FFSUBSYNC_PATH` | `/app/bazarr/bin/libs` |
| `SUBTITLE_GUARD_QUEUE` | unset; `1` makes `hook` queue its job, like `--queue` |
| `SUBTITLE_GUARD_WORKERS` | `2` |
| `SUBTITLE_GUARD_LIBRARY` | unset; folders to watch in `serve`, separated by `:` |
| `SUBTITLE_GUARD_WATCH_SECONDS` | `600` |
| `SUBTITLE_GUARD_UI_HOST` / `SUBTITLE_GUARD_UI_PORT` | `127.0.0.1` / `8484` |
| `SUBTITLE_GUARD_UI_TOKEN` | unset; when set, the UI asks for it |
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

Each decision carries a confidence from 0 to 100 and a band: `high` (80 and up), `medium` (60 to 79), `low` (below 60), `none` (nothing accepted). It takes the weakest of three measures (the margin over noise or over the null alignment, the margin of the worst 2-minute window, and for realigned files the share of lines in supported segments), then removes 10 points per weak minute. On the benchmark, accepted results are almost all `high`; the few `medium` or `low` ones are the cases that really are less precise, such as very sloppy source timing.

`needs-human` lists every subtitle whose last decision is not settled, has weak minutes, or has low confidence.

## Accuracy

`benchmarks/torture.py` misaligns known-good tracks in 42 ways and measures every line against its true time. On 12 tracks of 420 to 720 lines (French, Japanese and German, one dialogue-heavy series), 502 of 504 runs end as expected, with 97 to 100% of lines within one second when fixed, including different cuts, ad breaks adding up to 600 s, offsets up to 600 s, framerate and speed errors up to 5%, sloppy timing, merged or split lines, and dense stretches. Wrong-episode subtitles, random timings and offsets beyond ±1200 s are rejected. The two misses are fragmented dense stretches that are realigned correctly but rejected as a precaution. `benchmarks/tears_of_steel.py` builds a public corpus from the Blender open movie Tears of Steel.

## Limits

- Cumulative offsets beyond ±1200 s are rejected, not fixed; between ±400 s and ±1200 s a rejection costs a second, wider search (about 7 s instead of 2.5 s on a 4-core home server).
- Lines of a scene that exists in the subtitle but not in the video are placed as well as possible, not removed.
- Without any embedded subtitle track, timing cannot be verified; the ffsubsync fallback cannot fix a different cut.

## Roadmap

Done: package and tests, job queue with parallel workers, structured logs, confidence score, standalone mode, web UI.

Next: a reference from an OpenSubtitles hash match for videos without any embedded track; a published image.

## Development

```
uv venv --python 3.12 && uv pip install -e ".[test]"
.venv/bin/python -m pytest
```

## License

GPL-3.0-or-later. See `LICENSE`.
