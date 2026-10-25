import threading

from subtitle_guard import jobs


def test_jobs_are_claimed_in_order_and_once():
    first = jobs.enqueue("check", {"subtitle": "a.srt"})
    second = jobs.enqueue("check", {"subtitle": "b.srt"})
    claimed = jobs.claim(1)
    assert claimed["id"] == first and claimed["arguments"] == {"subtitle": "a.srt"} and claimed["status"] == "running"
    assert jobs.claim(2)["id"] == second
    assert jobs.claim(3) is None


def test_delayed_jobs_wait_for_their_time():
    jobs.enqueue("retry-check", {"subtitle": "a.srt"}, delay_seconds=60)
    assert jobs.claim(1) is None


def test_finish_fail_and_requeue():
    job_id = jobs.enqueue("check", {})
    jobs.claim(1)
    jobs.finish(job_id, {"decision": "fixed", "confidence": 93, "confidence_band": "high", "summary": "fixed"})
    assert jobs.job(job_id)["status"] == "done" and jobs.job(job_id)["confidence"] == 93
    other = jobs.enqueue("check", {})
    jobs.claim(1)
    jobs.fail(other, "OSError: missing file")
    assert jobs.job(other)["error"] == "OSError: missing file"
    assert jobs.requeue(other) and jobs.job(other)["status"] == "queued"
    assert not jobs.requeue(other)
    assert jobs.counts()["status"] == {"done": 1, "queued": 1}
    assert jobs.counts()["confidence"] == {"high": 1}


def test_running_jobs_are_recovered_after_a_crash():
    job_id = jobs.enqueue("check", {})
    jobs.claim(1)
    assert jobs.recover_running() == 1
    assert jobs.job(job_id)["status"] == "queued"


def test_parallel_workers_never_share_a_job():
    for number in range(60):
        jobs.enqueue("check", {"number": number})
    claimed, lock = [], threading.Lock()

    def drain(worker):
        while (job := jobs.claim(worker)) is not None:
            with lock:
                claimed.append(job["id"])

    threads = [threading.Thread(target=drain, args=(worker,)) for worker in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(claimed) == list(range(1, 61))
