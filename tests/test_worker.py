# SPDX-License-Identifier: GPL-3.0-or-later
import shutil
import subprocess

import numpy
import pytest
from synthetic import dialogue

from subtitle_guard import jobs, worker
from subtitle_guard.subtitles import parse_subtitle, timestamp


def test_check_job_records_the_decision(monkeypatch):
    monkeypatch.setattr(worker, "evaluate_and_fix", lambda video, subtitle, apply, neighbours: {
        "decision": "fixed", "confidence": 91, "confidence_band": "high", "summary": "realigned"})
    job_id = jobs.enqueue(jobs.KIND["check"], {"video": "v.mkv", "subtitle": "s.srt", "apply": False})
    assert worker.work_once(1)
    done = jobs.job(job_id)
    assert (done["status"], done["decision"], done["confidence"], done["confidence_band"], done["summary"]) == ("done", "fixed", 91, "high", "realigned")
    assert not worker.work_once(1)


def test_a_failing_job_is_recorded_and_the_worker_goes_on(monkeypatch):
    def broken(video, subtitle, apply, neighbours):
        raise OSError("video is gone")

    monkeypatch.setattr(worker, "evaluate_and_fix", broken)
    first = jobs.enqueue(jobs.KIND["check"], {"video": "v.mkv", "subtitle": "s.srt", "apply": False})
    unknown = jobs.enqueue("teleport", {})
    assert worker.work_once(1) and worker.work_once(1)
    assert jobs.job(first)["status"] == "failed" and jobs.job(first)["error"] == "OSError: video is gone"
    assert jobs.job(unknown)["error"] == "UnknownJobKindError: unknown job kind teleport"


def test_blacklist_job_schedules_a_later_check(monkeypatch):
    pending = {"provider": "gestdown", "subtitle_id": "x1"}
    monkeypatch.setattr(worker, "blacklist_for_retry", lambda video, subtitle: {"pending": pending, "attempts_before": 2})
    jobs.enqueue(jobs.KIND["blacklist"], {"video": "v.mkv", "subtitle": "s.srt"})
    assert worker.work_once(1)
    scheduled = [item for item in jobs.recent() if item["kind"] == jobs.KIND["retry_check"]]
    assert len(scheduled) == 1 and scheduled[0]["arguments"]["attempts_before"] == 2 and scheduled[0]["status"] == "queued"
    assert not worker.work_once(1)


def test_hook_job_runs_the_bazarr_flow(monkeypatch):
    seen = {}
    monkeypatch.setattr(worker, "handle_download", lambda **arguments: seen.update(arguments) or {"decision": "kept", "summary": "ok"})
    jobs.enqueue(jobs.KIND["hook"], {"video": "v.mkv", "subtitle": "s.srt", "language": "fr", "provider": "p", "subtitle_id": "1", "media_id": "3",
                                     "series_id": "20"})
    assert worker.work_once(1)
    assert seen["series_id"] == "20"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_a_queued_misaligned_subtitle_is_fixed_end_to_end(tmp_path):
    starts, ends = dialogue(5, minutes=6)
    lines = [f"{number}\n{timestamp(start)} --> {timestamp(end)}\nline {number}\n" for number, (start, end) in enumerate(zip(starts, ends, strict=False), 1)]
    (tmp_path / "reference.srt").write_text("\n".join(lines))
    video = tmp_path / "Show" / "Season 1" / "Show.S01E01.mkv"
    video.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=size=64x64:rate=25:duration={int(ends[-1]) + 5}",
                    "-i", str(tmp_path / "reference.srt"), "-map", "0", "-map", "1", "-c:v", "mpeg4", "-c:s", "srt", "-metadata:s:s:0", "language=eng",
                    str(video)], check=True)
    subtitle = video.with_suffix(".fr.srt")
    subtitle.write_text("\n".join(f"{number}\n{timestamp(start + 7)} --> {timestamp(end + 7)}\nligne {number}\n"
                                  for number, (start, end) in enumerate(zip(starts, ends, strict=False), 1)))
    job_id = jobs.enqueue(jobs.KIND["check"], {"video": str(video), "subtitle": str(subtitle), "apply": True})
    assert worker.work_once(1)
    done = jobs.job(job_id)
    assert done["status"] == "done" and done["decision"] == "fixed" and done["confidence_band"] == "high", (done["error"], done["summary"])
    fixed = numpy.array([start for start, _, _ in parse_subtitle(str(subtitle))["cues"]])
    assert numpy.max(numpy.abs(fixed - starts)) < 0.31


def test_a_rejection_with_only_a_reason_completes_as_a_job(tmp_path):
    from subtitle_guard.subtitles import timestamp
    subtitle = tmp_path / "late-ends.fr.srt"
    subtitle.write_text("".join(f"{number}\n{timestamp(number * 4.0)} --> {timestamp(number * 4.0 + 600)}\nline {number}\n\n" for number in range(1, 150)))
    job_id = jobs.enqueue(jobs.KIND["check"], {"video": "/missing.mkv", "subtitle": str(subtitle), "apply": True})
    worker.work_once(1)
    done = jobs.job(job_id)
    assert done["status"] == "done" and done["decision"] == "rejected" and "end times are wrong" in done["summary"]


def test_an_abandoned_watch_job_does_not_block_the_subtitle_forever(tmp_path):
    from subtitle_guard import library
    video, subtitle = tmp_path / "Movie.mkv", tmp_path / "Movie.fr.srt"
    video.write_bytes(b"v")
    subtitle.write_text("s")
    assert library.queue_if_changed(str(video), str(subtitle), [], True) == 1
    for _ in range(3):
        jobs.claim(1)
        jobs.recover_running(3, worker=1)
    assert jobs.recent()[0]["status"] == "failed"
    assert library.queue_if_changed(str(video), str(subtitle), [], True) == 1


def test_an_unreadable_neighbour_is_skipped(monkeypatch):
    from subtitle_guard import guard
    from subtitle_guard.errors import ProbeError

    def unreadable(video, hearing_impaired, count):
        raise ProbeError("Input/output error")
    monkeypatch.setattr(guard, "load_reference", unreadable)
    assert guard.find_better_episode([(1.0, 2.0, "x")], False, [("S01E02", "/media/E02.mkv")]) is None
