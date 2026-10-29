# SPDX-License-Identifier: GPL-3.0-or-later
import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from subtitle_guard import bazarr, jobs, state
from subtitle_guard.errors import ConfigError


class FakeBazarr:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def open(self, request, timeout):
        self.calls.append((request.get_method(), request.selector))
        if self.fail:
            raise urllib.error.URLError("connection refused")
        return Answer()


class Answer:
    status = 200

    def read(self, *size):
        return b'{"data": []}'

    def __enter__(self):
        return self

    def __exit__(self, *details):
        return False


def rejected_download(tmp_path, monkeypatch):
    video, subtitle = tmp_path / "Ep.mkv", tmp_path / "Ep.fr.srt"
    video.write_bytes(b"video")
    subtitle.write_text("subtitle")
    monkeypatch.setenv("BAZARR_API_KEY", "key")
    monkeypatch.setattr(bazarr, "evaluate_and_fix", lambda video, subtitle, apply, neighbours: {"decision": "rejected", "reason": "no match"})
    monkeypatch.setattr(bazarr, "series_neighbours", lambda series, video: ())
    return str(video), str(subtitle)


def test_a_rejected_download_under_workers_queues_the_blacklist(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    bazarr.handle_download(video, subtitle, "fr", "opensubtitles", "abc", "7", "2", queued=True)
    queued = jobs.recent()[0]
    assert queued["kind"] == jobs.KIND["blacklist"] and state.load_state(video, subtitle)["pending_retry"]["subtitle_id"] == "abc"


def test_a_failed_blacklist_keeps_the_pending_retry_and_a_success_clears_it(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    bazarr.handle_download(video, subtitle, "fr", "opensubtitles", "abc", "7", "2", queued=True)
    monkeypatch.setattr(bazarr, "OPENER", FakeBazarr(fail=True))
    with pytest.raises(OSError):
        bazarr.blacklist_for_retry(video, subtitle)
    assert state.load_state(video, subtitle)["pending_retry"]["subtitle_id"] == "abc"
    fake = FakeBazarr()
    monkeypatch.setattr(bazarr, "OPENER", fake)
    retry = bazarr.blacklist_for_retry(video, subtitle)
    assert retry["pending"]["subtitle_id"] == "abc" and fake.calls == [("POST", "/api/episodes/blacklist")]
    assert state.load_state(video, subtitle)["pending_retry"]["stage"] == "blacklisted"


def test_a_hook_queued_for_an_older_download_is_skipped(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    queued_hash = bazarr.file_hash(subtitle)
    Path(subtitle).write_text("a newer download")
    outcome = bazarr.handle_download(video, subtitle, "fr", "opensubtitles", "abc", "7", "2", content_hash=queued_hash, queued=True)
    assert outcome["decision"] == "skipped" and not jobs.recent()


def test_the_api_key_never_follows_a_redirect_to_another_host():
    handler = bazarr.SameOriginRedirect()
    request = urllib.request.Request("http://bazarr:6767/api/movies", headers={"X-API-KEY": "key"})
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, "http://evil.example/steal")
    assert handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, "http://bazarr:6767/api/movies/") is not None


def test_a_config_without_an_api_key_is_a_clear_error(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("general:\n  port: 6767\n")
    monkeypatch.delenv("BAZARR_API_KEY", raising=False)
    monkeypatch.setattr(bazarr, "BAZARR_CONFIG", str(config))
    with pytest.raises(ConfigError):
        bazarr.api_key()



def test_a_blacklist_run_again_does_not_post_twice_and_a_replaced_subtitle_cancels_it(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    bazarr.handle_download(video, subtitle, "fr", "opensubtitles", "abc", "7", "2", queued=True)
    fake = FakeBazarr()
    monkeypatch.setattr(bazarr, "OPENER", fake)
    assert bazarr.blacklist_for_retry(video, subtitle)["pending"]["stage"] == "blacklisted"
    assert bazarr.blacklist_for_retry(video, subtitle)["pending"]["stage"] == "blacklisted" and len(fake.calls) == 1
    other = tmp_path / "Other.mkv"
    other.write_bytes(b"other video")
    other_subtitle = tmp_path / "Other.fr.srt"
    other_subtitle.write_text("first download")
    bazarr.handle_download(str(other), str(other_subtitle), "fr", "opensubtitles", "xyz", "8", "2", queued=True)
    Path(other_subtitle).write_text("a newer download")
    assert bazarr.blacklist_for_retry(str(other), str(other_subtitle)) is None and len(fake.calls) == 1
    assert "pending_retry" not in state.load_state(str(other), str(other_subtitle))


def test_the_best_attempt_is_restored_even_after_bazarr_deleted_the_subtitle(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    best = tmp_path / "best.srt"
    best.write_text("best version")
    state.save_state(video, subtitle, {"attempts": [{"subtitle": subtitle, "provider": "opensubtitles", "after": {"matched": 0.8}, "best_copy": str(best)}],
                                       "pending_retry": {"subtitle": subtitle, "media_type": "movie", "media_id": 7, "content_hash": "gone"}})
    Path(subtitle).unlink()
    fake = FakeBazarr()
    monkeypatch.setattr(bazarr, "OPENER", fake)
    outcome = bazarr.finish_retry(video, subtitle, state.load_state(video, subtitle)["pending_retry"], 1)
    assert outcome["decision"] == "best-effort" and Path(subtitle).read_text() == "best version"
    assert fake.calls == [("PATCH", "/api/movies")] and "pending_retry" not in state.load_state(video, subtitle)


def test_an_https_bazarr_never_follows_a_downgrade_to_http():
    handler = bazarr.SameOriginRedirect()
    request = urllib.request.Request("https://bazarr:6767/api/movies", headers={"X-API-KEY": "key"})
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, "http://bazarr:6767/api/movies")
