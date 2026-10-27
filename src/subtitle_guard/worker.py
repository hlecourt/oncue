"""Run queued jobs in a few parallel worker processes; full file reads and audio decoding take turns through a lock."""

import multiprocessing
import os
import signal
import sys
import time
import traceback

from . import jobs
from .bazarr import RETRY_WAIT_SECONDS, blacklist_for_retry, finish_retry, handle_download
from .guard import evaluate_and_fix
from .logs import LEVEL, log, settings
from .outcome import describe
from .storage import report

WORKERS = int(os.environ.get("SUBTITLE_GUARD_WORKERS", "2"))
POLL_SECONDS = 2.0
SUPERVISION_SECONDS = 5.0


def run_workers(count=WORKERS):
    return supervise(worker_roles(count))


def worker_roles(count):
    return {f"worker-{number}": (work_forever, (number,)) for number in range(1, count + 1)}


def supervise(roles):
    settings["echo"], settings["source"] = True, "supervisor"
    recovered = jobs.recover_running()
    log("supervisor.started", f"supervisor started roles={','.join(sorted(roles))} recovered_jobs={recovered}", roles=sorted(roles), recovered=recovered)
    processes = {name: start(*role) for name, role in roles.items()}
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, lambda number, frame: shut_down(processes, signal.Signals(number).name))
    while True:
        time.sleep(SUPERVISION_SECONDS)
        for name, process in list(processes.items()):
            if not process.is_alive():
                log("supervisor.role-restarted", f"role {name} exited code={process.exitcode}, restarted", LEVEL["warning"], role=name,
                    exit_code=process.exitcode)
                processes[name] = start(*roles[name])


def shut_down(processes, signal_name):
    log("supervisor.stopping", f"{signal_name} received, stopping {len(processes)} roles", signal=signal_name, roles=sorted(processes))
    for process in processes.values():
        process.terminate()
    for process in processes.values():
        process.join(timeout=10)
    log("supervisor.stopped", "all roles stopped")
    sys.exit(0)


def start(target, arguments):
    process = multiprocessing.Process(target=target, args=arguments, daemon=True)
    process.start()
    return process


def work_forever(number):
    settings["echo"], settings["source"] = True, f"worker-{number}"
    log("worker.started", f"worker {number} started poll={POLL_SECONDS}s", worker=number)
    while True:
        if not work_once(number):
            time.sleep(POLL_SECONDS)


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
    except Exception as error:
        jobs.fail(job["id"], f"{type(error).__name__}: {error}")
        log("job.failed", f"job {job['id']} failed: {type(error).__name__}: {error}", LEVEL["error"], job=job["id"], kind=job["kind"], worker=number,
            subtitle=job["arguments"].get("subtitle"), error=f"{type(error).__name__}: {error}", trace=traceback.format_exc(limit=6))
        return True
    finally:
        settings["context"] = {}
    jobs.finish(job["id"], {**outcome, "summary": outcome.get("summary") or describe(outcome)})
    log("job.succeeded", f"job {job['id']} succeeded in {time.time() - started:.2f}s decision={outcome.get('decision')}", job=job["id"], kind=job["kind"], worker=number,
        subtitle=job["arguments"].get("subtitle"), decision=outcome.get("decision"), confidence=outcome.get("confidence"), band=outcome.get("confidence_band"),
        seconds=round(time.time() - started, 2))
    return True


def run_job(job):
    arguments = job["arguments"]
    if job["kind"] == jobs.KIND["hook"]:
        return handle_download(**arguments)
    if job["kind"] == jobs.KIND["check"]:
        outcome = evaluate_and_fix(arguments["video"], arguments["subtitle"], arguments["apply"], [tuple(pair) for pair in arguments.get("neighbours", [])])
        if arguments["apply"]:
            report(arguments["subtitle"], "check", outcome)
        return outcome
    if job["kind"] == jobs.KIND["blacklist"]:
        retry = blacklist_for_retry(arguments["video"], arguments["subtitle"])
        if retry is None:
            return {"decision": "nothing-pending", "summary": "no pending retry for this subtitle"}
        jobs.enqueue(jobs.KIND["retry_check"], {"video": arguments["video"], "subtitle": arguments["subtitle"], **retry}, delay_seconds=RETRY_WAIT_SECONDS)
        return {"decision": "blacklisted", "summary": f"blacklisted {retry['pending']['provider']} {retry['pending']['subtitle_id']}, waiting for another"}
    if job["kind"] == jobs.KIND["retry_check"]:
        return finish_retry(arguments["video"], arguments["subtitle"], arguments["pending"], arguments["attempts_before"])
    raise ValueError(f"unknown job kind {job['kind']}")
