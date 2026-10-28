# Configuration and operation

## Install without Docker

```
pip install "subtitle-guard[audio] @ git+https://github.com/hlecourt/subtitle-guard"
```

It needs Python 3.10 or later, `ffmpeg` and `ffprobe` on the PATH, and a POSIX system (Linux, macOS): it relies on `fcntl`
locks. The `audio` extra adds ffsubsync for videos without any embedded subtitle track.

## The Bazarr hook

In Bazarr, Settings > Subtitles > Post-processing, turn on post-processing and use this command (Bazarr runs it without a
shell). It queues a check and returns at once; the workers do the work:

```
/usr/bin/env PYTHONPATH=/opt/subtitle-guard /lsiopy/bin/python -m subtitle_guard hook --queue "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{subtitle_id}}" "{{episode_id}}" "{{series_id}}"
```

`/lsiopy/bin/python` is the Python of the linuxserver.io Bazarr image, which has numpy; `/opt/subtitle-guard` is where the
compose example mounts the package. Without `--queue`, the hook checks the subtitle inline before Bazarr goes on.

Turn off Bazarr's own audio sync (`use_subsync`): the guard falls back to ffsubsync by itself for videos without any embedded
track, and marks those results as not verified.

To check that it works, download a subtitle from Bazarr and look at the Log page: you should see `bazarr.hook-received`, then
`job.started` and `guard.checked` for that file.

## Without Bazarr

`scan-folder` checks every video under a folder against its external subtitles (`Movie.fr.srt`, `Show.S01E02.en.hi.ass`...).
Samples and hidden folders are skipped, and episode neighbours for the wrong-episode check come from `SxxEyy` or `NxNN` file
names. `watch` queues every new or changed subtitle under one or more folders; `serve` does the same for the folders in
`SUBTITLE_GUARD_LIBRARY`. Without Bazarr a rejected subtitle stays as it is and shows up for review; nothing is downloaded.

## Troubleshooting

- **Nothing happens when Bazarr downloads a subtitle**: the Log page shows no `bazarr.hook-received`. Check that post-processing
  is on, that the command path and `PYTHONPATH` are right inside the Bazarr container, and Bazarr's own log for the command output.
- **`health degraded failing=ffsubsync`** at start: ffsubsync is not importable; install the `audio` extra or set
  `SUBTITLE_GUARD_FFSUBSYNC_PATH`. Only videos without an embedded track need it.
- **"Cannot be checked: could not read the video"**: ffprobe failed on the file; the reason is in the `guard.checked` event.
- **Files owned by root or not writable**: run the container with `user:` set to the owner of the media.
- **`/ready` answers 503**: a worker stopped sending heartbeats; the Log page shows `supervisor.role-restarted` or `job.failed`.

## Settings

| Variable | Default |
|---|---|
| `SUBTITLE_GUARD_STATE` | `/config/subtitle-guard` (`/config` in the image): queue, history, log, caches, token |
| `SUBTITLE_GUARD_BACKUPS` | `/data/backup/subtitle-guard` (`/backup` in the image) |
| `SUBTITLE_GUARD_WORKERS` | `2` |
| `SUBTITLE_GUARD_QUEUE` | unset; `1` makes `hook` queue its job, like `--queue` |
| `SUBTITLE_GUARD_LIBRARY` | unset; folders to watch in `serve`, separated by `:` |
| `SUBTITLE_GUARD_WATCH_SECONDS` | `600` |
| `SUBTITLE_GUARD_ROOTS` | the watched folders; the UI only acts on subtitles under these folders |
| `SUBTITLE_GUARD_UI_HOST` / `SUBTITLE_GUARD_UI_PORT` | `127.0.0.1` / `8484` (`0.0.0.0` in the image) |
| `SUBTITLE_GUARD_UI_TOKEN` | unset; required beyond loopback, generated when unset |
| `SUBTITLE_GUARD_FFSUBSYNC_PATH` | `/app/bazarr/bin/libs`, where the linuxserver.io Bazarr image keeps ffsubsync; empty in the image, which installs it |
| `BAZARR_URL` | `http://localhost:6767` |
| `BAZARR_API_KEY` | read from `BAZARR_CONFIG` |
| `BAZARR_CONFIG` | `/config/config/config.yaml` |

## Running as a service

`subtitle-guard serve` runs a supervisor, `SUBTITLE_GUARD_WORKERS` worker processes (2 by default), the web UI and the folder
watch. Only one supervisor can run on a state directory. A worker that dies is restarted and its job requeued; a job that killed
its worker three times is marked failed. SIGTERM stops every role and the tools they started. Alignment runs in parallel; full
file reads and ffsubsync take turns through a lock so workers do not fight over a spinning disk. A rejected subtitle becomes a
`blacklist` job, then a `retry-check` 15 minutes later that keeps the best version tried if Bazarr found nothing else.

```
subtitle-guard status                     # jobs by status and confidence band
subtitle-guard jobs [failed]              # latest jobs
subtitle-guard job 42                     # one job in full, with its error if any
subtitle-guard retry 42                   # run a finished or failed job again
subtitle-guard scan-all --apply --queue   # check the whole library through the workers
subtitle-guard needs-human                # subtitles whose last check needs a person
subtitle-guard log 100 --problems         # the last warnings and errors
```

## Log

Everything goes to one log, `subtitle-guard.log` in the state directory (rotated at 10 MB under a lock), and to stdout for the
service, so `docker logs` shows it. Each line is one JSON event: `time`, `level` (`debug`, `info`, `warning`, `error`), `source`
(the process: `serve`, `supervisor`, `worker-1`, `web`, `watch`, `hook`, `cli`), `pid`, `event`, a short technical `message`, then
the metadata; events written while a job runs also carry its `job` id. Events are named `component.past-tense`, for example
`service.started` and `health.checked` at start, `bazarr.hook-received`, `bazarr.api-call`, `job.started`, `guard.checked` (with
the decision, confidence, alignment figures and changes), `guard.rewritten`, `job.succeeded`, `job.failed` (with the trace),
`review.shifted` for actions taken in the UI, and `supervisor.stopping`.
