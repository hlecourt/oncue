"""Run queued jobs in a few parallel worker processes; full file reads and audio decoding take turns through a lock."""

import multiprocessing
import os
import time
import traceback

from . import jobs
from .bazarr import RETRY_WAIT_SECONDS, blacklist_for_retry, finish_retry, handle_download
from .guard import evaluate_and_fix
from .logs import log, settings
from .outcome import describe
from .storage import report

WORKERS = int(os.environ.get("SUBTITLE_GUARD_WORKERS", "2"))
POLL_SECONDS = 2.0
SUPERVISION_SECONDS = 5.0


def run_workers(count=WORKERS):
    settings["echo"] = True
    log("workers-started", workers=count, recovered=jobs.recover_running())
    processes = {number: start_worker(number) for number in range(1, count + 1)}
    while True:
        time.sleep(SUPERVISION_SECONDS)
        for number, process in list(processes.items()):
            if not process.is_alive():
                log("worker-restarted", worker=number, exit_code=process.exitcode)
                processes[number] = start_worker(number)


def start_worker(number):
    process = multiprocessing.Process(target=work_forever, args=(number,), daemon=True)
    process.start()
    return process


def work_forever(number):
    settings["echo"] = True
    while True:
        if not work_once(number):
            time.sleep(POLL_SECONDS)


def work_once(number):
    job = jobs.claim(number)
    if job is None:
        return False
    started = time.time()
    log("job-started", job=job["id"], kind=job["kind"], worker=number, subtitle=job["arguments"].get("subtitle"))
    try:
        outcome = run_job(job)
    except Exception as error:
        jobs.fail(job["id"], f"{type(error).__name__}: {error}")
        log("job-failed", job=job["id"], kind=job["kind"], worker=number, error=f"{type(error).__name__}: {error}",
            trace=traceback.format_exc(limit=6))
        return True
    jobs.finish(job["id"], {**outcome, "summary": outcome.get("summary") or describe(outcome)})
    log("job-done", job=job["id"], kind=job["kind"], worker=number, decision=outcome.get("decision"), confidence=outcome.get("confidence"),
        band=outcome.get("confidence_band"), seconds=round(time.time() - started, 2))
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
