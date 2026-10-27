import io
import urllib.error
import urllib.request

import pytest

from subtitle_guard import bazarr, jobs, storage
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

    def read(self):
        return b'{"data": []}'


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
    assert queued["kind"] == jobs.KIND["blacklist"] and storage.load_state(video, subtitle)["pending_retry"]["subtitle_id"] == "abc"


def test_a_failed_blacklist_keeps_the_pending_retry_and_a_success_clears_it(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    bazarr.handle_download(video, subtitle, "fr", "opensubtitles", "abc", "7", "2", queued=True)
    monkeypatch.setattr(bazarr, "OPENER", FakeBazarr(fail=True))
    with pytest.raises(OSError):
        bazarr.blacklist_for_retry(video, subtitle)
    assert storage.load_state(video, subtitle)["pending_retry"]["subtitle_id"] == "abc"
    fake = FakeBazarr()
    monkeypatch.setattr(bazarr, "OPENER", fake)
    retry = bazarr.blacklist_for_retry(video, subtitle)
    assert retry["pending"]["subtitle_id"] == "abc" and fake.calls == [("POST", "/api/episodes/blacklist")]
    assert "pending_retry" not in storage.load_state(video, subtitle)


def test_a_hook_queued_for_an_older_download_is_skipped(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    queued_hash = bazarr.file_hash(subtitle)
    open(subtitle, "w").write("a newer download")
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
