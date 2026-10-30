# SPDX-License-Identifier: GPL-3.0-or-later
import io
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from oncue import bazarr, jobs, state, storage
from oncue.errors import ConfigError


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
    monkeypatch.setenv("ONCUE_MODE", "fix")
    monkeypatch.setattr(bazarr, "evaluate_and_fix", lambda video, subtitle, apply, neighbours: {"decision": "rejected", "reason": "no match"})
    monkeypatch.setattr(bazarr, "series_neighbours", lambda series, video: ())
    return str(video), str(subtitle)


def test_a_rejected_download_under_workers_queues_the_blacklist(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    bazarr.handle_download(video, subtitle, "fr", "opensubtitles", "abc", "7", "2", queued=True)
    queued = jobs.recent()[0]
    assert queued["kind"] == jobs.KIND["blacklist"] and state.load_state(video, subtitle)["pending_replacement"]["subtitle_id"] == "abc"


def test_a_failed_blacklist_keeps_the_pending_replacement_and_a_success_clears_it(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    bazarr.handle_download(video, subtitle, "fr", "opensubtitles", "abc", "7", "2", queued=True)
    monkeypatch.setattr(bazarr, "OPENER", FakeBazarr(fail=True))
    with pytest.raises(OSError):
        bazarr.blacklist_for_replacement(video, subtitle)
    assert state.load_state(video, subtitle)["pending_replacement"]["subtitle_id"] == "abc"
    fake = FakeBazarr()
    monkeypatch.setattr(bazarr, "OPENER", fake)
    retry = bazarr.blacklist_for_replacement(video, subtitle)
    assert retry["pending"]["subtitle_id"] == "abc" and fake.calls == [("POST", "/api/episodes/blacklist")]
    assert state.load_state(video, subtitle)["pending_replacement"]["stage"] == "blacklisted"


def test_a_replacement_saved_under_its_old_key_is_still_sent_and_saved_under_the_new_one(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    bazarr.handle_download(video, subtitle, "fr", "opensubtitles", "abc", "7", "2", queued=True)
    saved = state.load_state(video, subtitle)
    state.save_state(video, subtitle, {"attempts": saved["attempts"], "pending_retry": saved["pending_replacement"]})
    monkeypatch.setattr(bazarr, "OPENER", FakeBazarr())
    assert bazarr.blacklist_for_replacement(video, subtitle)["pending"]["subtitle_id"] == "abc"
    stored = storage.read_json(state.state_file(video, subtitle))
    assert "pending_retry" not in stored and stored["pending_replacement"]["stage"] == "blacklisted"


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


def test_the_api_key_is_read_from_the_auth_block_of_bazarrs_config():
    config = "general:\n  apikey: not-this-one\n  port: 6767\nauth:\n  type: null\n  apikey: 'abc123'  # comment\n  username: ''\nbackup:\n  apikey: nor-this\n"
    assert bazarr.auth_api_key(config) == "abc123"
    assert bazarr.auth_api_key("auth:\n  apikey:\n") is None
    assert bazarr.auth_api_key("auth:\n" + "  x: y\n" * 50_000) is None


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
    assert bazarr.blacklist_for_replacement(video, subtitle)["pending"]["stage"] == "blacklisted"
    assert bazarr.blacklist_for_replacement(video, subtitle)["pending"]["stage"] == "blacklisted" and len(fake.calls) == 1
    other = tmp_path / "Other.mkv"
    other.write_bytes(b"other video")
    other_subtitle = tmp_path / "Other.fr.srt"
    other_subtitle.write_text("first download")
    bazarr.handle_download(str(other), str(other_subtitle), "fr", "opensubtitles", "xyz", "8", "2", queued=True)
    Path(other_subtitle).write_text("a newer download")
    assert bazarr.blacklist_for_replacement(str(other), str(other_subtitle)) is None and len(fake.calls) == 1
    assert "pending_replacement" not in state.load_state(str(other), str(other_subtitle))


def test_the_best_attempt_is_restored_even_after_bazarr_deleted_the_subtitle(tmp_path, monkeypatch):
    video, subtitle = rejected_download(tmp_path, monkeypatch)
    best = tmp_path / "best.srt"
    best.write_text("best version")
    state.save_state(video, subtitle, {"attempts": [{"subtitle": subtitle, "provider": "opensubtitles", "after": {"matched": 0.8}, "copy": str(best)}],
                                       "pending_replacement": {"subtitle": subtitle, "media_type": "movie", "media_id": 7, "content_hash": "gone"}})
    Path(subtitle).unlink()
    fake = FakeBazarr()
    monkeypatch.setattr(bazarr, "OPENER", fake)
    outcome = bazarr.finish_replacement(video, subtitle, state.load_state(video, subtitle)["pending_replacement"], 1)
    assert outcome["decision"] == "best-effort" and Path(subtitle).read_text() == "best version"
    assert fake.calls == [("PATCH", "/api/movies")] and "pending_replacement" not in state.load_state(video, subtitle)


def test_an_https_bazarr_never_follows_a_downgrade_to_http():
    handler = bazarr.SameOriginRedirect()
    request = urllib.request.Request("https://bazarr:6767/api/movies", headers={"X-API-KEY": "key"})
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, "http://bazarr:6767/api/movies")


def test_a_series_scan_queues_each_subtitle_with_its_neighbour_episodes(monkeypatch, capsys):
    episodes = [{"season": 1, "episode": number, "path": f"/media/Show/S01E0{number}.mkv",
                 "subtitles": [{"path": f"/media/Show/S01E0{number}.fr.srt", "code2": "fr"}, {"path": None, "code2": "en"}]} for number in (2, 1, 3)]
    monkeypatch.setattr(bazarr, "bazarr_get", lambda path: {"data": episodes})
    assert bazarr.scan_series(7, True, queue=True) == 0
    queued = [job["arguments"] for job in reversed(jobs.recent())]
    assert [os.path.basename(item["subtitle"]) for item in queued] == ["S01E01.fr.srt", "S01E02.fr.srt", "S01E03.fr.srt"]
    assert [label for label, _ in queued[1]["neighbours"]] == ["S01E01", "S01E03"] and all(item["apply"] for item in queued)
    assert bazarr.series_neighbours(7, "/media/Show/S01E02.mkv") == [tuple(pair) for pair in queued[1]["neighbours"]]
    assert "S01E01 fr: queued job" in capsys.readouterr().out


def test_a_movie_scan_reports_each_result_and_goes_on_after_an_error(monkeypatch, capsys):
    from oncue.errors import ProbeError
    from oncue.reports import report_history
    movies = [{"title": "Broken", "path": "/media/Broken.mkv", "subtitles": [{"path": "/media/Broken.fr.srt", "code2": "fr"}]},
              {"title": "Fine", "path": "/media/Fine.mkv", "subtitles": [{"path": "/media/Fine.fr.srt", "code2": "fr"}]}]
    monkeypatch.setattr(bazarr, "bazarr_get", lambda path: {"data": movies})

    def evaluate(video, subtitle, apply, neighbours):
        if "Broken" in video:
            raise ProbeError("Invalid data found when processing input")
        return {"decision": "kept", "reason": "in sync"}
    monkeypatch.setattr(bazarr, "evaluate_and_fix", evaluate)
    assert bazarr.scan_movies(False) == 0
    printed = capsys.readouterr().out
    assert "Broken fr: error Invalid data" in printed and "Fine fr: oncue: kept" in printed
    assert [row["subtitle"] for row in report_history()] == ["/media/Fine.fr.srt"]


def test_a_hook_for_a_subtitle_that_is_already_gone_answers_instead_of_crashing(tmp_path):
    outcome = bazarr.handle_download(str(tmp_path / "Movie.mkv"), str(tmp_path / "Movie.fr.srt"), "fr", "opensubtitles", "1", "2")
    assert outcome["decision"] == "unverified" and "cannot be read" in outcome["reason"]
