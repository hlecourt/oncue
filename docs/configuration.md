# Configuration and operation

## Install without Docker

```
pip install "subtitle-guard[audio] @ git+https://github.com/hlecourt/subtitle-guard"
```

It needs Python 3.11 or later, `ffmpeg` and `ffprobe` on the PATH, and a POSIX system (Linux, macOS): it relies on `fcntl`
locks. The `audio` extra adds ffsubsync for videos without any embedded subtitle track.

## The Bazarr hook

In Bazarr, Settings > Subtitles > Post-processing, turn on post-processing and use this command (Bazarr runs it without a
shell). It queues a check and returns at once; the workers do the work:

```
/usr/bin/env PYTHONPATH=/config/subtitle-guard/hook /lsiopy/bin/python -m subtitle_guard hook --queue "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{subtitle_id}}" "{{episode_id}}" "{{series_id}}"
```

`/lsiopy/bin/python` is the Python of the linuxserver.io Bazarr image, which has numpy. `/config/subtitle-guard/hook` is
where `serve` copies the package at every start when `SUBTITLE_GUARD_HOOK_DIR` is set, as in the compose example, so the hook
always matches the running version. Without Docker, point `PYTHONPATH` at a clone's `src` folder instead. Without `--queue`,
the hook checks the subtitle inline before Bazarr goes on.

Turn off Bazarr's own audio sync (`use_subsync`): Bazarr runs it before the post-processing command, so it rewrites the file
without any confidence check and the guard never sees the subtitle as downloaded. The guard falls back to ffsubsync by itself
for videos without any embedded track, and marks those results as not verified.

Leave Bazarr's post-processing threshold off (`use_postprocessing_threshold`, and the movie one): when it is on, Bazarr runs the
command only for subtitles scored below it, so the best-scored downloads are never checked. Bazarr's score rates how well the
release name matches, not the timing.

To check that it works, download a subtitle from Bazarr and look at the Log page: you should see `bazarr.hook-received`, then
`job.started` and `guard.checked` for that file.

## Without Bazarr

`scan-folder` checks every video under a folder against its external subtitles (`Movie.fr.srt`, `Show.S01E02.en.hi.ass`...).
Samples and hidden folders are skipped, and episode neighbours for the wrong-episode check come from `SxxEyy` or `NxNN` file
names. `watch` queues every new or changed subtitle under one or more folders; `serve` does the same for the folders in
`SUBTITLE_GUARD_LIBRARY`. Without Bazarr a subtitle that is not in sync with high confidence stays as it is and shows up for
review, with its proposal when there is one; nothing is downloaded.

## Troubleshooting

- **Nothing happens when Bazarr downloads a subtitle**: the Log page shows no `bazarr.hook-received`. Check that post-processing
  is on, that the command path and `PYTHONPATH` are right inside the Bazarr container, and Bazarr's own log for the command output.
- **`health degraded failing=ffsubsync`** at start: ffsubsync is not importable; install the `audio` extra or set
  `SUBTITLE_GUARD_FFSUBSYNC_PATH`. Only videos without an embedded track need it.
- **"Cannot be checked: could not read the video"**: ffprobe failed on the file; the reason is in the `guard.checked` event.
- **Files owned by root or not writable**: run the container with `user:` set to the owner of the media.
- **`/ready` answers 503**: a worker stopped sending heartbeats; the Log page shows `supervisor.role-restarted` or `job.failed`.

## Settings

Every setting below except the folders, the state and the backups can also be changed in the UI; a variable that is set always
wins and shows as locked there.

| Variable | Default |
|---|---|
| `SUBTITLE_GUARD_STATE` | `/config/subtitle-guard` (`/config` in the image): queue, history, log, caches, UI account |
| `SUBTITLE_GUARD_BACKUPS` | `/data/backup/subtitle-guard` (`/backup` in the image) |
| `SUBTITLE_GUARD_AUDIO_AUTOMATIC` | unset; `1` lets a high-confidence audio sync be written without a person, once `calibrate-audio` found no false positive |
| `SUBTITLE_GUARD_KEEP_VERSIONS` / `SUBTITLE_GUARD_KEEP_DAYS` | `5` / `30`: backups kept per subtitle besides its first version |
| `SUBTITLE_GUARD_MODE` | `report`; `fix` lets the guard write high-confidence fixes on its own |
| `SUBTITLE_GUARD_WORKERS` | `2` |
| `SUBTITLE_GUARD_REPLACEMENTS` | `3`: other subtitles asked from Bazarr before keeping the best version |
| `SUBTITLE_GUARD_WEBHOOK_URL` / `SUBTITLE_GUARD_WEBHOOK_MINUTES` | unset / `15`: a digest of what reached Review, at most once per interval |
| `SUBTITLE_GUARD_URL_BASE` | unset; for example `/subtitle-guard` behind a reverse proxy, applied on restart |
| `SUBTITLE_GUARD_AUTH` | `forms`; `local` skips the login from private addresses, `external` trusts a reverse proxy that authenticates |
| `SUBTITLE_GUARD_API_PORT` | `8485`, the new API while it is built beside the current UI |
| `SUBTITLE_GUARD_AUDIO_CACHE_MB` | `2048`: light audio copies the editor plays, least recently played removed first |
| `SUBTITLE_GUARD_QUEUE` | unset; `1` makes `hook` queue its job, like `--queue` |
| `SUBTITLE_GUARD_HOOK_DIR` | unset; a folder Bazarr also sees, where `serve` copies the package for the hook |
| `SUBTITLE_GUARD_LIBRARY` | unset; folders to watch in `serve`, separated by `:` |
| `SUBTITLE_GUARD_WATCH_SECONDS` | `600` |
| `SUBTITLE_GUARD_ROOTS` | the watched folders; the UI only acts on subtitles under these folders |
| `SUBTITLE_GUARD_UI_HOST` / `SUBTITLE_GUARD_UI_PORT` | `127.0.0.1` / `8484` (`0.0.0.0` in the image) |
| `SUBTITLE_GUARD_UI_USER` / `SUBTITLE_GUARD_UI_PASSWORD` | `admin` / unset; beyond loopback a password is required, generated once when unset |
| `SUBTITLE_GUARD_FFSUBSYNC_PATH` | `/app/bazarr/bin/libs`, where the linuxserver.io Bazarr image keeps ffsubsync; empty in the image, which installs it |
| `BAZARR_URL` | `http://localhost:6767` |
| `BAZARR_API_KEY` | read from `BAZARR_CONFIG` |
| `BAZARR_CONFIG` | `/config/config/config.yaml` |

## Running as a service

`subtitle-guard serve` runs a supervisor, `SUBTITLE_GUARD_WORKERS` worker processes (2 by default), the web UI and the folder
watch. Only one supervisor can run on a state directory. A worker that dies is restarted and its job requeued; a job that killed
its worker three times is marked failed. SIGTERM stops every role and the tools they started. Alignment runs in parallel; full
file reads and ffsubsync take turns through a lock so workers do not fight over a spinning disk. A Bazarr download that is not
trusted becomes a `blacklist` job, then a `retry-check` 15 minutes later that keeps the best version tried if Bazarr found
nothing else.

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
