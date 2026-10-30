# SPDX-License-Identifier: GPL-3.0-or-later
from subtitle_guard import jobs, scans


def test_a_finished_scan_lets_a_new_one_start(monkeypatch):
    monkeypatch.setattr(scans, "bazarr_configured", lambda: False)
    first = scans.start_scan()
    assert scans.start_scan() == first
    jobs.claim(1)
    jobs.finish(jobs.recent()[0]["id"], 1, scans.run_scan(first))
    assert scans.scan(first)["status"] == "finished" and scans.scan(first)["total"] == 0
    second = scans.start_scan()
    assert second != first and scans.scan(first)["status"] == "finished" and scans.scan(second)["status"] == "listing"


def test_a_scan_with_checks_left_is_still_the_running_one(monkeypatch, tmp_path):
    monkeypatch.setattr(scans, "bazarr_configured", lambda: False)
    monkeypatch.setattr(scans, "scan_items", lambda: iter([(str(tmp_path / "a.mkv"), str(tmp_path / "a.fr.srt"), [])]))
    first = scans.start_scan()
    scans.run_scan(first)
    assert scans.scan(first)["status"] == "checking" and scans.start_scan() == first


def test_a_scan_whose_listing_failed_lets_a_new_one_start(monkeypatch):
    monkeypatch.setattr(scans, "bazarr_configured", lambda: False)
    first = scans.start_scan()
    job = jobs.claim(1)
    jobs.fail(job["id"], 1, "Bazarr did not answer")
    second = scans.start_scan()
    assert second != first and scans.scan(first)["status"] == "stopped"
