# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from subtitle_guard import bazarr, files, jobs, notifications, reports, scans, storage, worker
from subtitle_guard.api.app import create_app
from subtitle_guard.api.security import CSRF_HEADER

GOOD_SETTINGS = {"general": {"use_postprocessing": True, "postprocessing_cmd": "python -m subtitle_guard hook --queue {{episode}}",
                             "use_postprocessing_threshold": False, "use_postprocessing_threshold_movie": False}, "subsync": {"use_subsync": False}}


@pytest.fixture
def client():
    client = TestClient(create_app("127.0.0.1"), base_url="http://127.0.0.1")
    client.headers.update({CSRF_HEADER: client.get("/api/session").json()["csrf"]})
    return client


@pytest.mark.parametrize(("change", "codes"), [
    ({}, []),
    ({"general": {**GOOD_SETTINGS["general"], "use_postprocessing": False}}, ["postprocessing_off"]),
    ({"general": {**GOOD_SETTINGS["general"], "postprocessing_cmd": "/scripts/other.sh"}}, ["command_not_guard"]),
    ({"general": {**GOOD_SETTINGS["general"], "use_postprocessing_threshold_movie": True}}, ["postprocessing_threshold"]),
    ({"subsync": {"use_subsync": True}}, ["bazarr_sync_on"]),
])
def test_the_bazarr_setup_check_names_each_setting_that_keeps_subtitles_from_the_guard(client, monkeypatch, change, codes):
    monkeypatch.setattr(bazarr, "bazarr_get", lambda path: {**GOOD_SETTINGS, **change})
    answer = client.get("/api/system/bazarr").json()
    assert answer["reachable"] is True and [warning["code"] for warning in answer["warnings"]] == codes


def test_an_unreachable_bazarr_is_said_plainly(client, monkeypatch):
    def refuse(path):
        raise OSError("connection refused")
    monkeypatch.setattr(bazarr, "bazarr_get", refuse)
    answer = client.get("/api/system/bazarr").json()
    assert answer["reachable"] is False and "connection refused" in answer["warnings"][0]["message"]


def test_a_scan_through_the_watched_folders_queues_one_check_per_subtitle_and_can_stop(client, tmp_path, monkeypatch):
    for name in ("A", "B", "C"):
        folder = tmp_path / "media" / name
        folder.mkdir(parents=True)
        (folder / f"{name}.mkv").write_bytes(b"v")
        (folder / f"{name}.fr.srt").write_text("x")
    monkeypatch.setenv("SUBTITLE_GUARD_LIBRARY", str(tmp_path / "media"))
    monkeypatch.setattr(scans, "bazarr_configured", lambda: False)
    started = client.post("/api/scans").json()
    assert started["status"] == "listing" and started["source"] == "watched folders"
    assert client.post("/api/scans").json()["id"] == started["id"]
    scan_job = jobs.claim(1)
    jobs.finish(scan_job["id"], 1, worker.run_job(scan_job))
    progress = client.get("/api/scans/latest").json()
    assert progress["status"] == "checking" and progress["total"] == 3 and progress["done"] == 0
    assert all(job["arguments"]["apply"] is False for job in jobs.recent("queued"))
    stopped = client.post(f"/api/scans/{started['id']}/stop").json()
    assert stopped["status"] == "stopped" and jobs.recent("queued") == []


def test_the_webhook_sends_one_digest_per_interval_and_never_follows_a_redirect(tmp_path, monkeypatch):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204 if self.path == "/hook" else 302)
            if self.path != "/hook":
                self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()

        def log_message(self, *arguments):
            return
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SUBTITLE_GUARD_WEBHOOK_URL", f"http://127.0.0.1:{server.server_address[1]}/hook")
    assert notifications.send_due() is False
    reports.report(str(tmp_path / "A.fr.srt"), "scan", {"decision": "proposed", "confidence": 65, "confidence_band": "medium"})
    reports.report(str(tmp_path / "B.fr.srt"), "scan", {"decision": "kept", "confidence": 95, "confidence_band": "high"})
    state = storage.read_json(storage.state_path(notifications.STATE_FILE))
    storage.write_json(storage.state_path(notifications.STATE_FILE), {**state, "time": time.time() - 3600})
    assert notifications.send_due() is True and received[0]["count"] == 1 and received[0]["subtitles"][0]["name"] == "A.fr.srt"
    reports.report(str(tmp_path / "C.fr.srt"), "scan", {"decision": "rejected"})
    assert notifications.send_due() is False
    monkeypatch.setenv("SUBTITLE_GUARD_WEBHOOK_URL", f"http://127.0.0.1:{server.server_address[1]}/moved")
    storage.write_json(storage.state_path(notifications.STATE_FILE), {**storage.read_json(storage.state_path(notifications.STATE_FILE)), "time": 0})
    assert notifications.send_due() is False and len(received) == 2
    server.shutdown()


def test_the_system_page_and_the_backup_clean_up(client, tmp_path, monkeypatch):
    kept, gone = tmp_path / "kept.fr.srt", tmp_path / "gone.fr.srt"
    for subtitle in (kept, gone):
        subtitle.write_text("x")
        reports.report(str(subtitle), "scan", {"decision": "kept"})
    for _ in range(3):
        files.save_backup(str(kept), "original")
        files.save_backup(str(gone), "original")
    old = time.time() - 40 * 86400
    for folder in storage.backup_folders(str(kept)) + storage.backup_folders(str(gone)):
        os.utime(folder, (old, old))
    gone.unlink()
    monkeypatch.setenv("SUBTITLE_GUARD_KEEP_VERSIONS", "1")
    view = client.get("/api/system").json()
    assert set(view["checks"]) >= {"ffmpeg", "ffprobe", "ffsubsync", "bazarr"} and view["mode"] == "report" and view["backups_bytes"] > 0
    answer = client.post("/api/system/backups/clean-up").json()
    assert answer["removed"] == 4 and len(storage.backup_folders(str(kept))) == 2 and storage.backup_folders(str(gone)) == []
