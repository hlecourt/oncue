# Configuration and operation

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
