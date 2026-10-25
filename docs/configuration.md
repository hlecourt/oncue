# Configuration and operation

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
| `BAZARR_URL` | `http://localhost:6767` |
| `BAZARR_API_KEY` | read from `BAZARR_CONFIG` |
| `BAZARR_CONFIG` | `/config/config/config.yaml` |
