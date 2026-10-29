# SPDX-License-Identifier: GPL-3.0-or-later
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
  ui                                                                        web UI only (web extra)
  serve [--workers N] [--apply]                                             workers, web UI, and watch of SUBTITLE_GUARD_LIBRARY folders
  status                                                                    jobs by status and confidence band
  jobs [STATUS]                                                             latest jobs, optionally one status
  job ID                                                                    one job in full
  retry ID                                                                  run a finished or failed job again
  needs-human                                                               subtitles whose last check needs a person
  set-password [USER]                                                       set the UI account (default user admin); reads the password from stdin when piped
  calibrate-audio PATH [N]                                                  read-only: measure audio sync confidence on N videos that have a track
  log [N] [--problems]                                                      the last N events of the global log (default 50), or only warnings and errors
  try-another VIDEO SUBTITLE                                                internal: blacklist and let Bazarr retry

Settings (environment): SUBTITLE_GUARD_STATE, SUBTITLE_GUARD_BACKUPS, SUBTITLE_GUARD_FFSUBSYNC_PATH, SUBTITLE_GUARD_QUEUE=1 (hook queues),
SUBTITLE_GUARD_WORKERS, SUBTITLE_GUARD_WATCH_SECONDS, SUBTITLE_GUARD_LIBRARY,
SUBTITLE_GUARD_ROOTS, SUBTITLE_GUARD_HOOK_DIR (serve copies the package there for Bazarr's hook), SUBTITLE_GUARD_UI_HOST, SUBTITLE_GUARD_UI_PORT, SUBTITLE_GUARD_UI_USER, SUBTITLE_GUARD_UI_PASSWORD, BAZARR_URL, BAZARR_API_KEY or BAZARR_CONFIG.
"""

import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import configuration, jobs
from .api.auth import DEFAULT_USER, save_credentials
from .bazarr import (
    bazarr_get,
    handle_download,
    queue_enabled,
    scan_movies,
    scan_series,
    try_another_subtitle,
)
from .calibration import calibrate_audio
from .configuration import MODE
from .errors import ConfigError, SubtitleGuardError
from .files import file_hash
from .guard import evaluate_and_fix
from .health import report_startup
from .library import scan_folder, watch
from .logs import LEVEL, event_level, event_message, log, recent_events, settings
from .outcome import describe
from .reports import print_needs_human, report
from .storage import check_state_folder
from .worker import run_workers, supervise, worker_roles

HOOK_FIELDS = ("video", "subtitle", "language", "provider", "subtitle_id", "media_id", "series_id")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    command = sys.argv[1]
    arguments, flags, workers = parse_options(sys.argv[2:])
    apply, queue = flags["--apply"], flags["--queue"]
    try:
        check_state_folder()
    except ConfigError as error:
        print(f"subtitle-guard: {error}", file=sys.stderr)
        return 2
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
    if command in ("ui", "serve"):
        try:
            from .api import app as web
        except ImportError:
            print("subtitle-guard: the web UI needs the web extra: pip install 'subtitle-guard[web]'", file=sys.stderr)
            return 2
    if command == "ui":
        report_startup(command, ui_host=web.HOST, ui_port=web.PORT)
        web.serve_api()
        return 0
    if command == "serve":
        if os.environ.get("SUBTITLE_GUARD_HOOK_DIR"):
            install_hook_package(os.environ["SUBTITLE_GUARD_HOOK_DIR"])
        report_startup(command, workers=configuration.value("workers"), ui_host=web.HOST, ui_port=web.PORT, roots=configuration.value("watched_folders"),
                       applied=apply or configuration.value("mode") == MODE["fix"], queue=queue_enabled())
        return supervise(serve_roles(apply, workers, web.serve_api), lambda: serve_roles(apply, workers, web.serve_api))
    if command == "calibrate-audio" and arguments:
        calibrate_audio(arguments[0], int(arguments[1]) if len(arguments) > 1 and arguments[1].isdigit() else None)
        return 0
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
    if command == "set-password":
        return set_password(arguments[0] if arguments else DEFAULT_USER)
    if command == "log":
        return print_log(int(arguments[0]) if arguments else 50, flags["--problems"])
    if command == "try-another" and len(arguments) == 2:
        return try_another_subtitle(*arguments)
    print(__doc__)
    return 2


def parse_options(raw):
    arguments, flags, workers, index = [], {"--apply": False, "--queue": False, "--problems": False}, None, 0
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
    fields = dict(zip(HOOK_FIELDS, arguments, strict=False))
    log("bazarr.hook-received", f"post-processing hook lang={fields['language']} provider={fields['provider']} id={fields['subtitle_id']} "
        f"mode={'queued' if queue else 'inline'}", **fields)
    if queue:
        print(f"subtitle-guard: queued job {jobs.enqueue(jobs.KIND['hook'], {**fields, 'content_hash': received_hash(fields['subtitle'])})}")
        return 0
    print(describe(handle_download(*arguments)))
    return 0


def set_password(user):
    interactive = sys.stdin.isatty()
    password = getpass.getpass(f"New UI password for {user}: ") if interactive else sys.stdin.readline().rstrip("\n")
    if interactive and getpass.getpass("The same password again: ") != password:
        print("The two passwords differ, nothing was changed.")
        return 1
    try:
        save_credentials(user, password)
    except ConfigError as error:
        print(f"subtitle-guard: {error}")
        return 1
    print(f"Password saved for {user}. A running UI uses it at once and signs everyone out.")
    return 0


def print_log(count, problems_only):
    events = [event for event in recent_events() if not problems_only or event_level(event) in (LEVEL["warning"], LEVEL["error"])][:count]
    for event in reversed(events):
        subtitle = f"  [{os.path.basename(event['subtitle'])}]" if event.get("subtitle") else ""
        print(f"{event['time']}  {event_level(event).upper():7s} {event.get('source', ''):10s} {event['event']:28s} {event_message(event)}{subtitle}")
    return 0


def received_hash(subtitle):
    try:
        return file_hash(subtitle)
    except (OSError, SubtitleGuardError):
        return None


def lower_io_priority():
    if shutil.which("ionice"):
        subprocess.run(["ionice", "-c2", "-n7", "-p", str(os.getpid())], capture_output=True, check=False)


def install_hook_package(folder):
    target = os.path.join(folder, "subtitle_guard")
    os.makedirs(folder, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".subtitle_guard.", dir=folder)
    shutil.copytree(os.path.dirname(os.path.abspath(__file__)), os.path.join(staging, "subtitle_guard"), ignore=shutil.ignore_patterns("__pycache__"))
    if os.path.exists(target):
        os.rename(target, os.path.join(staging, "previous"))
    os.rename(os.path.join(staging, "subtitle_guard"), target)
    shutil.rmtree(staging)
    log("service.hook-installed", f"package copied for the Bazarr hook into {folder}", folder=folder)


def serve_roles(apply, workers, serve_web):
    roles = {**worker_roles(workers or configuration.value("workers")), "web": (serve_web, ())}
    folders = configuration.value("watched_folders")
    if folders:
        roles["watch"] = (watch, (tuple(folders), apply or configuration.value("mode") == MODE["fix"]))
    return roles
