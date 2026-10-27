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

Everything goes to one global log, `subtitle-guard.log` in the state directory (rotated at 10 MB), and to stdout for the workers so `docker logs` shows it. Each line is one JSON event: `time`, `level` (`debug`, `info`, `warning`, `error`), `source` (the process: `serve`, `supervisor`, `worker-1`, `web`, `watch`, `hook`, `cli`), `pid`, `event`, a short technical `message`, then the metadata; events written while a job runs also carry its `job` id. Events are named `component.past-tense`: `service.started` (version, settings) and `health.checked` (ffmpeg, ffprobe, ffsubsync, writable folders, Bazarr) when a long-running command starts, `supervisor.started`, `supervisor.role-restarted`, `supervisor.stopping` and `supervisor.stopped` (SIGTERM or SIGINT), `worker.started`, `web.listening`, `web.auth-denied`, `bazarr.hook-received`, `bazarr.api-call` and `bazarr.api-error` (never with the key), `bazarr.blacklisted`, `bazarr.replacement-*`, `bazarr.best-attempt-restored`, `job.queued`, `job.started`, `job.succeeded`, `job.failed` (with the trace), `guard.checked` (decision, confidence, alignment figures, changes) and `guard.rewritten`, `reference.extracted`, `reference.index-unavailable`, `reference.packet-scan`, `reference.cache-corrupt`, `audio.synced`, `audio.sync-rejected`, `review.*` (every action taken in the UI) and `watch.*`. The **Log** page shows it newest first with a search; each line opens on its metadata as JSON with a Copy button. `subtitle-guard log [N] [--problems]` prints it. Jobs live in `jobs.sqlite3` next to it.

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
