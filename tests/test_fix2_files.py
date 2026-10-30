# SPDX-License-Identifier: GPL-3.0-or-later
import os
import threading
import time

import pytest

from subtitle_guard import files, library, review
from subtitle_guard.errors import UnsafePathError


def test_a_fifo_named_like_a_subtitle_is_refused_without_blocking(tmp_path):
    fifo = tmp_path / "Movie.fr.srt"
    os.mkfifo(fifo)
    outcome = {}

    def read():
        try:
            files.read_regular(str(fifo))
        except UnsafePathError as error:
            outcome["error"] = error
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout=5)
    assert not reader.is_alive() and "not a regular file" in str(outcome["error"])


def test_an_allowed_folder_itself_is_inside_the_allowed_folders_but_never_written(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [os.path.realpath(media)])
    assert files.inside_allowed_folders(str(media)) and files.inside_allowed_folders(str(media / "Movie.srt"))
    assert not files.inside_allowed_folders(str(tmp_path)) and not files.inside_allowed_folders(str(tmp_path / "media-other"))
    with pytest.raises(UnsafePathError):
        files.replace_file(str(media), b"x")


def leftover_in(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Movie.mkv").write_bytes(b"")
    leftover = folder / f".Movie.fr.srt.abc{files.TEMPORARY_SUFFIX}"
    leftover.write_text("partial")
    os.utime(leftover, (time.time() - 2 * library.LEFTOVER_SECONDS,) * 2)
    return leftover


def test_leftover_temporary_files_are_only_removed_inside_the_allowed_folders(tmp_path, monkeypatch):
    inside, outside = leftover_in(tmp_path / "media" / "Movie"), leftover_in(tmp_path / "elsewhere")
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [os.path.realpath(tmp_path / "media")])
    list(library.media_folders(str(tmp_path)))
    assert not inside.exists() and outside.exists()
    monkeypatch.setattr(files, "ALLOWED_FOLDERS", [])
    list(library.media_folders(str(tmp_path)))
    assert outside.exists()


def test_review_markup_cleaning_stays_linear_on_a_long_run_of_brackets():
    started = time.time()
    review.clean("<" * 1_000_000)
    review.clean("{" * 1_000_000)
    # the quadratic pattern took minutes here; a slow CI runner needs over a second for the linear one
    assert time.time() - started < 10
    assert review.clean("<i>hi</i> {\\an8}there") == "hi there"
