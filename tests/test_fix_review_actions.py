# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from subtitle_guard import reports, review_actions, storage
from subtitle_guard.errors import EditRefusedError, StaleVersionError
from subtitle_guard.files import file_hash

CUES = [(10.0, 11.0, "one"), (20.0, 21.0, "two"), (30.0, 31.0, "three")]


def test_changes_add_up_on_the_lines_they_cover():
    timings = review_actions.retimed_timings(CUES, None, [(0, 1, 0.5), (1, 2, -1.0)])
    assert timings == [(10.5, 11.5, "one"), (19.5, 20.5, "two"), (29.0, 30.0, "three")]


def test_the_guard_fix_is_the_base_the_changes_move():
    proposal = {"timings": [[9.0, 10.0], [19.0, 20.0], [29.0, 30.0]]}
    assert review_actions.retimed_timings(CUES, proposal, [(2, 2, 1.0)]) == [(9.0, 10.0, "one"), (19.0, 20.0, "two"), (30.0, 31.0, "three")]
    assert review_actions.retimed_timings(CUES, proposal, [])[0] == (9.0, 10.0, "one")


@pytest.mark.parametrize("changes", [[(0, 3, 1.0)], [(2, 1, 1.0)], [], [(0, 0, 0.0)], [(0, 0, -11.0)]])
def test_a_change_that_cannot_be_written_is_refused(changes):
    with pytest.raises(EditRefusedError):
        review_actions.retimed_timings(CUES, None, changes)


def test_a_write_on_a_version_the_page_did_not_see_changes_nothing(tmp_path):
    subtitle = tmp_path / "Movie.fr.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
    reports.report(str(subtitle), "scan", {"decision": "rejected"})
    for action in (lambda: review_actions.delete_subtitle(str(subtitle), "0" * 40),
                   lambda: review_actions.retime("/Movie.mkv", str(subtitle), "0" * 40, False, [(0, 0, 1.0)])):
        with pytest.raises(StaleVersionError):
            action()
    assert subtitle.exists() and storage.backup_folders(str(subtitle)) == []
    review_actions.delete_subtitle(str(subtitle), file_hash(str(subtitle)))
    assert not subtitle.exists() and [name for folder in storage.backup_folders(str(subtitle)) for name in os.listdir(folder)] == ["deleted.srt"]
