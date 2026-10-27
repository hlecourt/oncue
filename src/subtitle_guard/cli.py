"""subtitle-guard: check subtitle timing against a track embedded in the video, and repair or replace it.

Commands:
  hook [--queue] VIDEO SUBTITLE LANGUAGE PROVIDER SUBTITLE_ID MEDIA_ID [SERIES_ID]   Bazarr post-processing entry point
  check VIDEO SUBTITLE [--apply]                                            one file now, dry run unless --apply
  enqueue VIDEO SUBTITLE [--apply]                                          one file, through the queue
  scan-series SERIES_ID [--apply] [--queue]                                 every external subtitle of a Bazarr series
  scan-movies [--apply] [--queue]                                           every external subtitle of every Bazarr movie
  scan-all [--apply] [--queue]                                              every Bazarr series, then every movie
  scan-folder PATH [--apply] [--queue]                                      every external subtitle under a media folder, without Bazarr
  watch PATH... [--apply]                                                   queue every new or changed subtitle under media folders
  worker [--workers N]                                                      run queued jobs with N parallel workers
  ui                                                                        web UI only
  serve [--workers N] [--apply]                                             workers, web UI, and watch of SUBTITLE_GUARD_LIBRARY folders
  status                                                                    jobs by status and confidence band
  jobs [STATUS]                                                             latest jobs, optionally one status
  job ID                                                                    one job in full
  retry ID                                                                  run a finished or failed job again
  needs-human                                                               subtitles whose last check needs a person
  log [N] [--problems]                                                      the last N events of the global log (default 50), or only warnings and errors
  try-another VIDEO SUBTITLE                                                internal: blacklist and let Bazarr retry

Settings (environment): SUBTITLE_GUARD_STATE, SUBTITLE_GUARD_BACKUPS, SUBTITLE_GUARD_FFSUBSYNC_PATH, SUBTITLE_GUARD_QUEUE=1 (hook queues),
SUBTITLE_GUARD_WORKERS, SUBTITLE_GUARD_WATCH_SECONDS, SUBTITLE_GUARD_LIBRARY,
SUBTITLE_GUARD_UI_HOST, SUBTITLE_GUARD_UI_PORT, SUBTITLE_GUARD_UI_TOKEN, BAZARR_URL, BAZARR_API_KEY or BAZARR_CONFIG.
"""

import json
import os
import subprocess
import sys

from . import jobs
from .bazarr import bazarr_get, file_hash, handle_download, queue_enabled, scan_movies, scan_series, try_another_subtitle
from .guard import evaluate_and_fix
from .health import report_startup
from .library import scan_folder, watch
from .logs import LEVEL, event_level, event_message, log, recent_events, settings
from .outcome import describe
from .reports import print_needs_human, report
from .web import HOST as UI_HOST, PORT as UI_PORT, serve_ui
from .worker import WORKERS, run_workers, supervise, worker_roles

HOOK_FIELDS = ("video", "subtitle", "language", "provider", "subtitle_id", "media_id", "series_id")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    command = sys.argv[1]
    arguments, flags, workers = parse_options(sys.argv[2:])
    apply, queue = flags["--apply"], flags["--queue"]
    lower_io_priority()
    if command == "hook" and len(arguments) >= 6:
        return hook(arguments, queue or queue_enabled())
    if command == "check" and len(arguments) == 2:
        outcome = evaluate_and_fix(arguments[0], arguments[1], apply)
        report(arguments[1], "check", outcome, applied=apply)
        print(describe(outcome))
        return 0
    if command == "enqueue" and len(arguments) == 2:
        print(f"queued job {jobs.enqueue(jobs.KIND['check'], {'video': arguments[0], 'subtitle': arguments[1], 'apply': apply})}")
        return 0
    if command == "scan-series" and len(arguments) == 1:
        return scan_series(int(arguments[0]), apply, queue)
    if command == "scan-movies":
        return scan_movies(apply, queue)
    if command == "scan-all":
        for series in bazarr_get("/api/series?start=0&length=-1")["data"]:
            scan_series(series["sonarrSeriesId"], apply, queue)
        return scan_movies(apply, queue)
    if command == "scan-folder" and len(arguments) == 1:
        return scan_folder(arguments[0], apply, queue)
    if command == "watch" and arguments:
        report_startup(command, roots=arguments, applied=apply)
        return watch(arguments, apply)
    if command == "worker":
        report_startup(command, workers=workers)
        return run_workers(workers)
    if command == "ui":
        report_startup(command, ui_host=UI_HOST, ui_port=UI_PORT)
        return serve_ui()
    if command == "serve":
        roles = {**worker_roles(workers), "web": (serve_ui, ())}
        roots = [root for root in os.environ.get("SUBTITLE_GUARD_LIBRARY", "").split(os.pathsep) if root]
        report_startup(command, workers=workers, ui_host=UI_HOST, ui_port=UI_PORT, roots=roots, applied=apply, queue=queue_enabled())
        if roots:
            roles["watch"] = (watch, (roots, apply))
        return supervise(roles)
    if command == "status":
        print(json.dumps(jobs.counts(), indent=1))
        return 0
    if command == "jobs":
        for item in jobs.recent(arguments[0] if arguments else None):
            print(f"{item['id']:6d} {item['status']:8s} {item['kind']:12s} {item['decision'] or '':14s} {item['confidence'] if item['confidence'] is not None else '':>3} "
                  f"{item['confidence_band'] or '':6s} {item['finished'] or item['created']}  {os.path.basename(item['arguments'].get('subtitle', ''))}")
        return 0
    if command == "job" and len(arguments) == 1:
        item = jobs.job(int(arguments[0]))
        print(json.dumps(item, indent=1) if item else "no such job")
        return 0 if item else 1
    if command == "retry" and len(arguments) == 1:
        requeued = jobs.requeue(int(arguments[0]))
        print("requeued" if requeued else "only a finished or failed job can be retried")
        return 0 if requeued else 1
    if command == "needs-human":
        return print_needs_human()
    if command == "log":
        return print_log(int(arguments[0]) if arguments else 50, flags["--problems"])
    if command == "try-another" and len(arguments) == 2:
        return try_another_subtitle(*arguments)
    print(__doc__)
    return 2


def parse_options(raw):
    arguments, flags, workers, index = [], {"--apply": False, "--queue": False, "--problems": False}, WORKERS, 0
    while index < len(raw):
        if raw[index] in flags:
            flags[raw[index]] = True
        elif raw[index] == "--workers" and index + 1 < len(raw):
            workers = int(raw[index + 1])
            index += 1
        else:
            arguments.append(raw[index])
        index += 1
    return arguments, flags, workers


def hook(arguments, queue):
    settings["source"] = "hook"
    fields = dict(zip(HOOK_FIELDS, arguments))
    log("bazarr.hook-received", f"post-processing hook lang={fields['language']} provider={fields['provider']} id={fields['subtitle_id']} "
        f"mode={'queued' if queue else 'inline'}", **fields)
    if queue:
        print(f"subtitle-guard: queued job {jobs.enqueue(jobs.KIND['hook'], {**fields, 'content_hash': file_hash(fields['subtitle']) if os.path.isfile(fields['subtitle']) else None})}")
        return 0
    print(describe(handle_download(*arguments)))
    return 0


def print_log(count, problems_only):
    events = [event for event in recent_events() if not problems_only or event_level(event) in (LEVEL["warning"], LEVEL["error"])][:count]
    for event in reversed(events):
        subtitle = f"  [{os.path.basename(event['subtitle'])}]" if event.get("subtitle") else ""
        print(f"{event['time']}  {event_level(event).upper():7s} {event.get('source', ''):10s} {event['event']:28s} {event_message(event)}{subtitle}")
    return 0


def lower_io_priority():
    try:
        subprocess.run(["ionice", "-c2", "-n7", "-p", str(os.getpid())], capture_output=True)
    except OSError:
        pass
