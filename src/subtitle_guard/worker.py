"""Run queued jobs in a few parallel worker processes; full file reads and audio decoding take turns through a lock."""

import fcntl
import multiprocessing
import os
import signal
import sys
import threading
import time
import traceback

from . import jobs
from .bazarr import (
    RETRY_WAIT_SECONDS,
    blacklist_for_retry,
    finish_retry,
    handle_download,
)
from .errors import UnknownJobKindError
from .guard import evaluate_and_fix
from .heartbeats import HEARTBEAT_SECONDS
from .library import FAILED, watch_signature
from .logs import LEVEL, log, settings
from .outcome import describe
from .reports import needs_review, report
from .review import diagnose
from .storage import state_path, write_json

WORKERS = int(os.environ.get("SUBTITLE_GUARD_WORKERS", "2"))
POLL_SECONDS = 2.0
SUPERVISION_SECONDS = 5.0
STOP_SECONDS = 10
MAX_ATTEMPTS = 3


def run_workers(count=WORKERS):
    return supervise(worker_roles(count))


def worker_roles(count):
    return {f"worker-{number}": (work_forever, (number,)) for number in range(1, count + 1)}


def supervise(roles):
    settings["echo"], settings["source"] = True, "supervisor"
    lock = single_supervisor_lock()
    if lock is None:
        log("supervisor.refused", "another supervisor already runs on this state directory, exiting", LEVEL["error"], state=state_path())
        return 1
    requeued, abandoned = jobs.recover_running(MAX_ATTEMPTS)
    log("supervisor.started", f"supervisor started roles={','.join(sorted(roles))} requeued_jobs={requeued} abandoned_jobs={abandoned}", roles=sorted(roles),
        requeued=requeued, abandoned=abandoned)
    write_json(state_path("supervisor.json"), {"pid": os.getpid(), "workers": sorted(name for name in roles if name.startswith("worker-"))})
    processes = {name: start(*role) for name, role in roles.items()}
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, lambda number, frame: shut_down(processes, signal.Signals(number).name))
    while True:
        time.sleep(SUPERVISION_SECONDS)
        for name, process in list(processes.items()):
            if not process.is_alive():
                processes[name] = restart(name, process, roles[name])


def single_supervisor_lock():
    os.makedirs(state_path(), exist_ok=True)
    handle = open(state_path("supervisor.lock"), "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def restart(name, process, role):
    recovered = jobs.recover_running(MAX_ATTEMPTS, int(name.split("-")[1])) if name.startswith("worker-") else (0, 0)
    log("supervisor.role-restarted", f"role {name} exited code={process.exitcode}, restarted requeued_jobs={recovered[0]} abandoned_jobs={recovered[1]}",
        LEVEL["warning"], role=name, exit_code=process.exitcode, requeued=recovered[0], abandoned=recovered[1])
    return start(*role)


def shut_down(processes, signal_name):
    log("supervisor.stopping", f"{signal_name} received, stopping {len(processes)} roles", signal=signal_name, roles=sorted(processes))
    for process in processes.values():
        signal_group(process, signal.SIGTERM)
    deadline = time.time() + STOP_SECONDS
    for process in processes.values():
        process.join(timeout=max(0, deadline - time.time()))
    survivors = [process for process in processes.values() if process.is_alive()]
    for process in survivors:
        signal_group(process, signal.SIGKILL)
        process.join(timeout=STOP_SECONDS)
    if os.path.exists(state_path("supervisor.json")):
        os.remove(state_path("supervisor.json"))
    log("supervisor.stopped", f"all roles stopped killed={len(survivors)}", killed=len(survivors))
    sys.exit(0)


def signal_group(process, number):
    try:
        os.killpg(process.pid, number)
    except ProcessLookupError:
        return


def start(target, arguments):
    process = multiprocessing.Process(target=run_role, args=(target, arguments), daemon=True)
    process.start()
    return process


def run_role(target, arguments):
    os.setpgid(0, 0)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    target(*arguments)


def work_forever(number):
    settings["echo"], settings["source"] = True, f"worker-{number}"
    log("worker.started", f"worker {number} started poll={POLL_SECONDS}s", worker=number)
    threading.Thread(target=beat, args=(number,), daemon=True).start()
    while True:
        if not work_once(number):
            time.sleep(POLL_SECONDS)


def beat(number):
    path = state_path("heartbeats", f"worker-{number}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    while True:
        with open(path, "w") as handle:
            handle.write(str(os.getpid()))
        time.sleep(HEARTBEAT_SECONDS)


def work_once(number):
    job = jobs.claim(number)
    if job is None:
        return False
    started = time.time()
    log("job.started", f"job {job['id']} started kind={job['kind']} attempt={job['attempts']}", job=job["id"], kind=job["kind"], worker=number,
        subtitle=job["arguments"].get("subtitle"))
    settings["context"] = {"job": job["id"]}
    try:
        outcome = run_job(job)
        jobs.finish(job["id"], {**outcome, "summary": outcome.get("summary") or describe(outcome)})
    except Exception as error:
        jobs.fail(job["id"], f"{type(error).__name__}: {error}")
        log("job.failed", f"job {job['id']} failed: {type(error).__name__}: {error}", LEVEL["error"], job=job["id"], kind=job["kind"], worker=number,
            subtitle=job["arguments"].get("subtitle"), error=f"{type(error).__name__}: {error}", trace=traceback.format_exc(limit=6))
        return True
    finally:
        settings["context"] = {}
    log("job.succeeded", f"job {job['id']} succeeded in {time.time() - started:.2f}s decision={outcome.get('decision')}", job=job["id"], kind=job["kind"], worker=number,
        subtitle=job["arguments"].get("subtitle"), decision=outcome.get("decision"), confidence=outcome.get("confidence"), band=outcome.get("confidence_band"),
        seconds=round(time.time() - started, 2))
    return True


def check(arguments):
    try:
        outcome = evaluate_and_fix(arguments["video"], arguments["subtitle"], arguments["apply"], [tuple(pair) for pair in arguments.get("neighbours", [])])
    except Exception:
        if arguments.get("watched"):
            jobs.remember(arguments["subtitle"], FAILED + watch_signature(arguments["video"], arguments["subtitle"], arguments["apply"]))
        raise
    report(arguments["subtitle"], "watch" if arguments.get("watched") else "check", outcome, applied=arguments["apply"])
    prepare_review(arguments["video"], arguments["subtitle"], outcome, arguments["apply"])
    if arguments.get("watched"):
        jobs.remember(arguments["subtitle"], watch_signature(arguments["video"], arguments["subtitle"], arguments["apply"]))
    return outcome


def prepare_review(video, subtitle, outcome, applied):
    row = {"applied": applied, "decision": outcome["decision"], "weak_minutes": outcome.get("weak_minutes"), "band": outcome.get("confidence_band")}
    if not needs_review(row):
        return
    try:
        diagnose(video, subtitle)
    except Exception as error:
        log("worker.review-not-prepared", f"could not prepare the review page: {type(error).__name__}: {error}", LEVEL["warning"], subtitle=subtitle)


def run_job(job):
    arguments = job["arguments"]
    if job["kind"] == jobs.KIND["hook"]:
        return handle_download(**arguments, queued=True)
    if job["kind"] == jobs.KIND["check"]:
        return check(arguments)
    if job["kind"] == jobs.KIND["blacklist"]:
        retry = blacklist_for_retry(arguments["video"], arguments["subtitle"])
        if retry is None:
            return {"decision": "nothing-pending", "summary": "no pending retry for this subtitle"}
        jobs.enqueue(jobs.KIND["retry_check"], {"video": arguments["video"], "subtitle": arguments["subtitle"], **retry}, delay_seconds=RETRY_WAIT_SECONDS)
        return {"decision": "blacklisted", "summary": f"blacklisted {retry['pending']['provider']} {retry['pending']['subtitle_id']}, waiting for another"}
    if job["kind"] == jobs.KIND["retry_check"]:
        return finish_retry(arguments["video"], arguments["subtitle"], arguments["pending"], arguments["attempts_before"])
    raise UnknownJobKindError(f"unknown job kind {job['kind']}")
