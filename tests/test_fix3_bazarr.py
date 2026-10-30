# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from oncue import bazarr, state, storage
from oncue.files import file_hash


@pytest.fixture
def waiting_replacement(tmp_path, monkeypatch):
    video, subtitle, best = tmp_path / "Movie.mkv", tmp_path / "Movie.fr.srt", tmp_path / "best.srt"
    video.write_bytes(b"video")
    subtitle.write_text("rejected download")
    best.write_text("best attempt")
    pending = {"subtitle": str(subtitle), "media_type": "movie", "media_id": 7, "series_id": None, "provider": "a", "subtitle_id": "1",
               "language": "fr", "content_hash": file_hash(str(subtitle)), "stage": bazarr.REPLACEMENT_STAGE["blacklisted"]}
    state.save_state(str(video), str(subtitle), {"attempts": [{"subtitle": str(subtitle), "provider": "a", "confidence": 40, "copy": str(best)}],
                                                 "pending_replacement": pending})
    monkeypatch.setattr(bazarr, "rescan", lambda pending: None)
    monkeypatch.setattr(bazarr, "evaluate_and_fix", lambda video, subtitle, apply, neighbours: {"decision": "kept"})
    return str(video), subtitle, pending


def test_an_edit_saved_after_the_last_check_is_never_overwritten_by_the_best_attempt(waiting_replacement, monkeypatch):
    video, subtitle, pending = waiting_replacement
    monkeypatch.setattr(bazarr, "another_arrived", lambda video, subtitle, pending, attempts_before: False)
    subtitle.write_text("saved from the editor")
    bazarr.finish_replacement(video, str(subtitle), pending, 1)
    assert subtitle.read_text() == "saved from the editor" and storage.backup_folders(str(subtitle)) == []


def test_the_best_attempt_comes_back_when_nobody_touched_the_subtitle(waiting_replacement):
    video, subtitle, pending = waiting_replacement
    bazarr.finish_replacement(video, str(subtitle), pending, 1)
    assert subtitle.read_text() == "best attempt" and "pending_replacement" not in state.load_state(video, str(subtitle))


def test_an_old_replacement_timer_leaves_a_newer_request_in_place(waiting_replacement, monkeypatch):
    video, subtitle, pending = waiting_replacement
    subtitle.write_text("download b")
    newer = {**pending, "provider": "b", "subtitle_id": "2", "content_hash": file_hash(str(subtitle)), "stage": bazarr.REPLACEMENT_STAGE["requested"]}

    def second_download(current):
        current["attempts"].append({"subtitle": str(subtitle), "provider": "b"})
        current["pending_replacement"] = newer

    state.update_state(video, str(subtitle), second_download)
    assert bazarr.finish_replacement(video, str(subtitle), pending, 1)["decision"] == "replaced"
    assert state.load_state(video, str(subtitle))["pending_replacement"] == newer
    posted = []
    monkeypatch.setattr(bazarr, "bazarr_post", lambda path, form: posted.append(form))
    assert bazarr.blacklist_for_replacement(video, str(subtitle))["pending"]["subtitle_id"] == "2" and posted[0]["subs_id"] == "2"
