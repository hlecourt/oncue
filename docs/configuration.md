# Configuration and operation

## Install without Docker

```
pip install "oncue[web,audio] @ https://github.com/hlecourt/oncue/releases/download/v0.1.0/oncue-0.1.0-py3-none-any.whl"
```

Install the wheel attached to a release: it carries the built web UI. A clone has no built UI until you build it (see
[CONTRIBUTING.md](../CONTRIBUTING.md)).

It needs Python 3.11 or later, `ffmpeg` and `ffprobe` on the PATH, and a POSIX system (Linux, macOS): it relies on `fcntl`
locks. The `web` extra adds the UI (FastAPI and uvicorn); the Bazarr hook, the CLI and the workers only need numpy. The `audio`
extra adds ffsubsync for videos without any embedded subtitle track. Set `ONCUE_ROOTS` to your media folders (or
`ONCUE_LIBRARY` without Bazarr): nothing is written anywhere until it is set, whatever the mode.

## The Bazarr hook

In Bazarr, Settings > Subtitles > Post-processing, turn on post-processing and use this command (Bazarr runs it without a
shell). It queues a check and returns at once; the workers do the work:

```
/usr/bin/env PYTHONPATH=/config/oncue/hook /lsiopy/bin/python -m oncue hook --queue "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{subtitle_id}}" "{{episode_id}}" "{{series_id}}"
```

`/lsiopy/bin/python` is the Python of the linuxserver.io Bazarr image, which has numpy. `/config/oncue/hook` is
where `serve` copies the package at every start when `ONCUE_HOOK_DIR` is set, as in the compose example, so the hook
always matches the running version. Without Docker, point `PYTHONPATH` at a clone's `src` folder instead. Without `--queue`,
the hook checks the subtitle inline before Bazarr goes on.

Turn off Bazarr's own audio sync (`use_subsync`): Bazarr runs it before the post-processing command, so it rewrites the file
without any confidence check and OnCue never sees the subtitle as downloaded. OnCue falls back to ffsubsync by itself
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
`ONCUE_LIBRARY`. Without Bazarr a subtitle that is not in sync with high confidence stays as it is and shows up for
review, with its proposal when there is one; nothing is downloaded.

## Health and API

The UI talks to a JSON API under `/api`, described at `/api/openapi.json` (a login is needed). Scripts send the API key in an
`X-Api-Key` header. `/health` says the server is up and `/ready` checks the database and every worker; both also answer under
the URL base.

## Troubleshooting

- **Nothing happens when Bazarr downloads a subtitle**: the Log page shows no `bazarr.hook-received`. Check that post-processing
  is on, that the command path and `PYTHONPATH` are right inside the Bazarr container, and Bazarr's own log for the command output.
- **`health degraded failing=ffsubsync`** at start: ffsubsync is not importable; install the `audio` extra or set
  `ONCUE_FFSUBSYNC_PATH`. Only videos without an embedded track need it.
- **"Cannot be checked: could not read the video"**: ffprobe failed on the file; the reason is in the `guard.checked` event.
- **Files owned by root or not writable**: run the container with `user:` set to the owner of the media.
- **`/ready` answers 503**: a worker stopped sending heartbeats; the Log page shows `supervisor.role-restarted` or `job.failed`.

## Settings

Every setting below except the folders, the state and the backups can also be changed in the UI; a variable that is set always
wins and shows as locked there.

| Variable | Default |
|---|---|
| `ONCUE_STATE` | `/config/oncue` (`/config` in the image): queue, history, log, caches, UI account |
| `ONCUE_BACKUPS` | `/data/backup/oncue` (`/backup` in the image) |
| `ONCUE_AUDIO_AUTOMATIC` | unset; `1` lets a high-confidence audio sync be written without a person, once `calibrate-audio` found no false positive |
| `ONCUE_TEXT_CHECK` | unset; `1` turns on the text check (see below) |
| `ONCUE_TEXT_MODEL` | unset; the folder of the sentence model the text check uses |
| `ONCUE_TEXT_SEAM_AUDIO` | unset; `1` lets the text check read a few seconds of audio around the lines it cannot place between two passages (speech activity as evidence). Off by default: on the development set it added nothing and once sent a seam line to the wrong side (the true place was a pause) |
| `ONCUE_TEXT_SEAM_SPEECH` | unset; `1` lets the text check's refine passes transcribe a few seconds around the lines still in doubt next to a written passage (faster-whisper base, int8, installed separately) and use the recognised words as evidence, never as a write on its own |
| `ONCUE_TEXT_SPEECH_CHECK` | unset; `1` lets the text check measure the subtitle and the reference against the speech of the video (webrtcvad on the centre channel of the reference's language, about 32 s per episode on a 4-core home server, no PyTorch, no Whisper): a move that would sit off the speech is refused, a slow drift the text and the speech both measure can be written, a small passage offset (0.65 to 2.5 s) is only offered in Review (no small-move gate passed its Learn-then-Test on the development data), and a measurable residual above 0.5 s blocks "verified". A proposal from timing alone is corrected for the reference's measured lead. It fails closed: with it on and no audio, no webrtcvad, no audio stream known to be in the reference's language (several streams and none tagged with it, or a single stream tagged with another), a read that takes over 15 minutes, or no settled measure of the file's final timing, the file is never verified and no such move is made. With it off, the text alone can verify a file, and cannot tell a reference track that is off its own audio |
| `ONCUE_KEEP_VERSIONS` / `ONCUE_KEEP_DAYS` | `5` / `30`: backups kept per subtitle besides its first version |
| `ONCUE_MODE` | `report`; `fix` lets OnCue write high-confidence fixes on its own. A fix found from timing alone stays a proposal unless the text check is on (`ONCUE_TEXT_CHECK`): timing cannot tell a line from its neighbour |
| `ONCUE_WORKERS` | `2` |
| `ONCUE_REPLACEMENTS` | `3`: other subtitles asked from Bazarr before keeping the best version |
| `ONCUE_WEBHOOK_URL` / `ONCUE_WEBHOOK_MINUTES` | unset / `15`: a digest of what reached Review, at most once per interval |
| `ONCUE_URL_BASE` | unset; for example `/oncue` behind a reverse proxy, applied on restart |
| `ONCUE_AUTH` | `forms`; `local` skips the login from private addresses, `external` trusts a reverse proxy that authenticates |
| `ONCUE_TRUSTED_PROXIES` | unset; reverse proxies whose `X-Forwarded-For` is believed, as addresses or networks separated by commas (`172.18.0.0/16`) |
| `ONCUE_AUDIO_CACHE_MB` | `2048`: light sound copies and video windows the editor plays, least recently played removed first |
| `ONCUE_QUEUE` | unset; `1` makes `hook` queue its job, like `--queue` |
| `ONCUE_HOOK_DIR` | unset; a folder Bazarr also sees, where `serve` copies the package for the hook |
| `ONCUE_LIBRARY` | unset; folders to watch in `serve`, separated by `:` |
| `ONCUE_WATCH_SECONDS` | `600` |
| `ONCUE_ROOTS` | the allowed folders: subtitles are only changed under these folders, and none at all when unset |
| `ONCUE_UI_HOST` / `ONCUE_UI_PORT` | `127.0.0.1` / `8484` (`0.0.0.0` in the image) |
| `ONCUE_UI_USER` / `ONCUE_UI_PASSWORD` | `admin` / unset; beyond loopback a password is required, generated once when unset |
| `ONCUE_FFSUBSYNC_PATH` | `/app/bazarr/bin/libs`, where the linuxserver.io Bazarr image keeps ffsubsync; empty in the image, which installs it |
| `BAZARR_URL` | `http://localhost:6767` |
| `BAZARR_API_KEY` | read from `BAZARR_CONFIG` |
| `BAZARR_CONFIG` | `/config/config/config.yaml` |

## Text check

Off by default. When it is on, every check also reads what the lines say: a multilingual sentence model matches each line with
the reference line it translates, which timing alone cannot do (see [how OnCue decides](how-it-works.md) and ADR 0007). It writes
a passage only when its calibrated safety is high and it stays in order with the lines around it, shows a file as in sync only when
no line is in doubt, lists everything else in Review with its minutes, and reports reference dialogue that has no subtitle line.

It needs the `text` extra (ONNX Runtime and a tokenizer, no PyTorch): `pip install 'oncue[text]'`, or an image built with
`--build-arg ONCUE_TEXT=1`, which also adds Tesseract to read picture tracks. The model is not in the image: download a
sentence-transformers model that publishes an ONNX export once, into the state volume, quantise it to int8 once
(`onnxruntime.quantization.quantize_dynamic`), and point `ONCUE_TEXT_MODEL` at it. Nothing is downloaded at run time. Only the two
calibrated families can write: multilingual-e5-small (the light default) and LaBSE; any other model only proposes.

```
hf download intfloat/multilingual-e5-small --include "onnx/model.onnx" "tokenizer.json" "modules.json" "sentence_bert_config.json" "1_Pooling/*" --local-dir /config/models/e5small
echo '{"prompts": {"query": "query: "}}' > /config/models/e5small/config_sentence_transformers.json
ONCUE_TEXT_CHECK=1 ONCUE_TEXT_MODEL=/config/models/e5small
```

| Model (int8) | Size | 22-minute episode, 4-core home server, 2 threads |
|---|---|---|
| `intfloat/multilingual-e5-small` (MIT), the default | 112 MB | about 4 s, about 0.5 GB |
| `sentence-transformers/LaBSE` (Apache-2.0), optional | 449 MB | about 12 s, about 0.9 GB |

With the check on, OnCue writes passages it can vouch for and leaves the rest to a person: on the held-out evaluation set it made
159 automatic writes with none wrong, but most files still end in Review for a few lines no text could settle (see ADR 0007).

The reference text comes from a text track directly. A picture track (PGS) is read by Tesseract when it is installed (a few
minutes per episode, once, then cached), or from an SRT you made elsewhere, saved as
`<state>/reference-texts/<video file name>.<track index>.srt`, which is used first.

## Video in the editor

The editor plays the video in short windows made on demand around the playhead, kept in the same cache as the sound copies. A
picture in 8-bit H.264 is copied as is, so it shows as soon as the editor opens. Any other picture (HEVC, AV1, 10-bit) is encoded
again in 720p, 10 seconds at a time: a few seconds per window on a recent processor, more on a small one, with still frames
in the meantime.

## Running as a service

`oncue serve` runs a supervisor, `ONCUE_WORKERS` worker processes (2 by default), the web UI and the folder
watch. Only one supervisor can run on a state directory. A worker that dies is restarted and its job requeued; a job that killed
its worker three times is marked failed. SIGTERM stops every role and the tools they started. Alignment runs in parallel; full
file reads and ffsubsync take turns through a lock so workers do not fight over a spinning disk. A Bazarr download that is not
trusted becomes a `blacklist` job, then a `replacement-check` 15 minutes later that keeps the best version tried if Bazarr found
nothing else.

```
oncue status                     # jobs by status and confidence band
oncue jobs [failed]              # latest jobs
oncue job 42                     # one job in full, with its error if any
oncue retry 42                   # run a finished or failed job again
oncue scan-all --apply --queue   # check the whole library through the workers
oncue needs-human                # subtitles whose last check needs a person
oncue log 100 --problems         # the last warnings and errors
```

## Log

Everything goes to one log, `oncue.log` in the state directory (rotated at 10 MB under a lock), and to stdout for the
service, so `docker logs` shows it. Each line is one JSON event: `time`, `level` (`debug`, `info`, `warning`, `error`), `source`
(the process: `serve`, `supervisor`, `worker-1`, `web`, `watch`, `hook`, `cli`), `pid`, `event`, a short technical `message`, then
the metadata; events written while a job runs also carry its `job` id. Events are named `component.past-tense`, for example
`service.started` and `health.checked` at start, `bazarr.hook-received`, `bazarr.api-call`, `job.started`, `guard.checked` (with
the decision, confidence, alignment figures and changes), `guard.rewritten`, `job.succeeded`, `job.failed` (with the trace),
`review.retimed` for timing saved from the editor, and `supervisor.stopping`.
