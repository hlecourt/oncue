# SPDX-License-Identifier: GPL-3.0-or-later
"""oncue: check subtitle timing against a track embedded in the video, and repair or replace it.

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
  serve [--workers N] [--apply]                                             workers, web UI, and watch of ONCUE_LIBRARY folders
  status                                                                    jobs by status and confidence band
  jobs [STATUS]                                                             latest jobs, optionally one status
  job ID                                                                    one job in full
  retry ID                                                                  run a finished or failed job again
  needs-human                                                               subtitles whose last check needs a person
  set-password [USER]                                                       set the UI account (default user admin); reads the password from stdin when piped
  calibrate-audio PATH [N]                                                  read-only: measure audio sync confidence on N videos that have a track
  log [N] [--problems]                                                      the last N events of the global log (default 50), or only warnings and errors
  try-another VIDEO SUBTITLE                                                internal: blacklist and let Bazarr retry

Settings (environment): ONCUE_STATE, ONCUE_BACKUPS, ONCUE_FFSUBSYNC_PATH, ONCUE_QUEUE=1 (hook queues),
ONCUE_WORKERS, ONCUE_WATCH_SECONDS, ONCUE_LIBRARY,
ONCUE_ROOTS, ONCUE_HOOK_DIR (serve copies the package there for Bazarr's hook), ONCUE_UI_HOST, ONCUE_UI_PORT, ONCUE_UI_USER, ONCUE_UI_PASSWORD, BAZARR_URL, BAZARR_API_KEY or BAZARR_CONFIG.
"""

import getpass
import json
import math
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
from .errors import ConfigError, OnCueError
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
DEFAULT_LOG_EVENTS = 50
UNBOUNDED = math.inf


class UsageError(OnCueError):
    pass


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    try:
        arguments, flags, workers = parse_options(sys.argv[2:])
    except UsageError as error:
        return refuse(error)
    try:
        check_state_folder()
    except ConfigError as error:
        print(f"oncue: {error}", file=sys.stderr)
        return 2
    lower_io_priority()
    minimum, maximum, run = COMMANDS.get(sys.argv[1], (0, 0, None))
    if run is None or not minimum <= len(arguments) <= maximum:
        print(__doc__)
        return 2
    try:
        return run(arguments, flags, workers)
    except UsageError as error:
        return refuse(error)


def refuse(error):
    print(f"oncue: {error}", file=sys.stderr)
    print(__doc__)
    return 2


def parse_options(raw):
    arguments, flags, workers, index = [], {"--apply": False, "--queue": False, "--problems": False}, None, 0
    while index < len(raw):
        if raw[index] in flags:
            flags[raw[index]] = True
        elif raw[index] == "--workers":
            if index + 1 == len(raw):
                raise UsageError("--workers needs a number")
            workers = whole_number(raw[index + 1])
            index += 1
        else:
            arguments.append(raw[index])
        index += 1
    return arguments, flags, workers


def whole_number(text):
    try:
        return int(text)
    except ValueError:
        raise UsageError(f"{text!r} is not a whole number") from None


def run_hook(arguments, flags, workers):
    return hook(arguments, flags["--queue"] or queue_enabled())


def run_check(arguments, flags, workers):
    outcome = evaluate_and_fix(arguments[0], arguments[1], flags["--apply"])
    report(arguments[1], "check", outcome, applied=flags["--apply"])
    print(describe(outcome))
    return 0


def run_enqueue(arguments, flags, workers):
    print(f"queued job {jobs.enqueue(jobs.KIND['check'], {'video': arguments[0], 'subtitle': arguments[1], 'apply': flags['--apply']})}")
    return 0


def run_scan_series(arguments, flags, workers):
    return scan_series(whole_number(arguments[0]), flags["--apply"], flags["--queue"])


def run_scan_movies(arguments, flags, workers):
    return scan_movies(flags["--apply"], flags["--queue"])


def run_scan_all(arguments, flags, workers):
    for series in bazarr_get("/api/series?start=0&length=-1")["data"]:
        scan_series(series["sonarrSeriesId"], flags["--apply"], flags["--queue"])
    return scan_movies(flags["--apply"], flags["--queue"])


def run_scan_folder(arguments, flags, workers):
    return scan_folder(arguments[0], flags["--apply"], flags["--queue"])


def run_watch(arguments, flags, workers):
    report_startup("watch", folders=arguments, applied=flags["--apply"])
    return watch(arguments, flags["--apply"])


def run_worker(arguments, flags, workers):
    report_startup("worker", workers=workers)
    return run_workers(workers)


def run_ui(arguments, flags, workers):
    web = web_module()
    if web is None:
        return 2
    report_startup("ui", ui_host=web.HOST, ui_port=web.PORT)
    web.serve_api()
    return 0


def run_serve(arguments, flags, workers):
    web = web_module()
    if web is None:
        return 2
    apply = flags["--apply"]
    if os.environ.get("ONCUE_HOOK_DIR"):
        install_hook_package(os.environ["ONCUE_HOOK_DIR"])
    report_startup("serve", workers=configuration.value("workers"), ui_host=web.HOST, ui_port=web.PORT, folders=configuration.value("watched_folders"),
                   applied=apply or configuration.value("mode") == MODE["fix"], queue=queue_enabled())
    return supervise(serve_roles(apply, workers, web.serve_api), lambda: serve_roles(apply, workers, web.serve_api))


def web_module():
    try:
        from .api import app as web
    except ImportError:
        print("oncue: the web UI needs the web extra: pip install 'oncue[web]'", file=sys.stderr)
        return None
    return web


def run_calibrate_audio(arguments, flags, workers):
    calibrate_audio(arguments[0], int(arguments[1]) if len(arguments) > 1 and arguments[1].isdigit() else None)
    return 0


def run_status(arguments, flags, workers):
    print(json.dumps(jobs.counts(), indent=1))
    return 0


def run_jobs(arguments, flags, workers):
    for item in jobs.recent(arguments[0] if arguments else None):
        print(f"{item['id']:6d} {item['status']:8s} {item['kind']:12s} {item['decision'] or '':14s} {item['confidence'] if item['confidence'] is not None else '':>3} "
              f"{item['confidence_band'] or '':6s} {item['finished'] or item['created']}  {os.path.basename(item['arguments'].get('subtitle', ''))}")
    return 0


def run_job(arguments, flags, workers):
    item = jobs.job(whole_number(arguments[0]))
    print(json.dumps(item, indent=1) if item else "no such job")
    return 0 if item else 1


def run_retry(arguments, flags, workers):
    requeued = jobs.requeue(whole_number(arguments[0]))
    print("requeued" if requeued else "only a finished or failed job can be retried")
    return 0 if requeued else 1


def run_needs_human(arguments, flags, workers):
    return print_needs_human()


def run_set_password(arguments, flags, workers):
    return set_password(arguments[0] if arguments else DEFAULT_USER)


def run_log(arguments, flags, workers):
    return print_log(whole_number(arguments[0]) if arguments else DEFAULT_LOG_EVENTS, flags["--problems"])


def run_try_another(arguments, flags, workers):
    return try_another_subtitle(*arguments)


def hook(arguments, queue):
    settings["source"] = "hook"
    fields = dict(zip(HOOK_FIELDS, arguments, strict=False))
    log("bazarr.hook-received", f"post-processing hook lang={fields['language']} provider={fields['provider']} id={fields['subtitle_id']} "
        f"mode={'queued' if queue else 'inline'}", **fields)
    if queue:
        print(f"oncue: queued job {jobs.enqueue(jobs.KIND['hook'], {**fields, 'content_hash': received_hash(fields['subtitle'])})}")
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
        print(f"oncue: {error}")
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
    except (OSError, OnCueError):
        return None


def lower_io_priority():
    if shutil.which("ionice"):
        subprocess.run(["ionice", "-c2", "-n7", "-p", str(os.getpid())], capture_output=True, check=False)


def install_hook_package(folder):
    target = os.path.join(folder, "oncue")
    os.makedirs(folder, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".oncue.", dir=folder)
    shutil.copytree(os.path.dirname(os.path.abspath(__file__)), os.path.join(staging, "oncue"), ignore=shutil.ignore_patterns("__pycache__"))
    if os.path.exists(target):
        os.rename(target, os.path.join(staging, "previous"))
    os.rename(os.path.join(staging, "oncue"), target)
    shutil.rmtree(staging)
    log("service.hook-installed", f"package copied for the Bazarr hook into {folder}", folder=folder)


def serve_roles(apply, workers, serve_web):
    roles = {**worker_roles(workers or configuration.value("workers")), "web": (serve_web, ())}
    folders = configuration.value("watched_folders")
    if folders:
        roles["watch"] = (watch, (tuple(folders), apply or configuration.value("mode") == MODE["fix"]))
    return roles


COMMANDS = {
    "hook": (len(HOOK_FIELDS) - 1, len(HOOK_FIELDS), run_hook),
    "check": (2, 2, run_check),
    "enqueue": (2, 2, run_enqueue),
    "scan-series": (1, 1, run_scan_series),
    "scan-movies": (0, UNBOUNDED, run_scan_movies),
    "scan-all": (0, UNBOUNDED, run_scan_all),
    "scan-folder": (1, 1, run_scan_folder),
    "watch": (1, UNBOUNDED, run_watch),
    "worker": (0, UNBOUNDED, run_worker),
    "ui": (0, UNBOUNDED, run_ui),
    "serve": (0, UNBOUNDED, run_serve),
    "calibrate-audio": (1, UNBOUNDED, run_calibrate_audio),
    "status": (0, UNBOUNDED, run_status),
    "jobs": (0, UNBOUNDED, run_jobs),
    "job": (1, 1, run_job),
    "retry": (1, 1, run_retry),
    "needs-human": (0, UNBOUNDED, run_needs_human),
    "set-password": (0, UNBOUNDED, run_set_password),
    "log": (0, UNBOUNDED, run_log),
    "try-another": (2, 2, run_try_another),
}
