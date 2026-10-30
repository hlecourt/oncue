# SPDX-License-Identifier: GPL-3.0-or-later
import itertools

import pytest
from media import video_with_reference

from oncue import matroska, review
from oncue.errors import MatroskaError

ID = matroska.MATROSKA_ID
CLUSTER = 0x1F43B675


def element(identifier, payload):
    return identifier.to_bytes((identifier.bit_length() + 7) // 8, "big") + b"\x01" + len(payload).to_bytes(7, "big") + payload


def number(identifier, value, width=4):
    return element(identifier, value.to_bytes(width, "big"))


def simple_block(data):
    return element(ID["simple_block"], b"\x81\x00\x00\x00" + data)


def group_block(data):
    return element(ID["block_group"], element(ID["block"], b"\x81\x00\x00\x00" + data))


def write_matroska(path, blocks, cues):
    """One track, one cluster holding the blocks; each cue is (milliseconds, index of the block it points to)."""
    info, tracks = element(ID["info"], b""), element(ID["tracks"], element(ID["track_entry"], number(ID["track_number"], 1, 1)))
    offsets = list(itertools.accumulate([0, *(len(block) for block in blocks)]))

    def seek_head(info_at, tracks_at, cues_at):
        seeks = [element(ID["seek"], element(ID["seek_id"], identifier.to_bytes(4, "big")) + number(ID["seek_position"], at, 8))
                 for identifier, at in ((ID["info"], info_at), (ID["tracks"], tracks_at), (ID["cues"], cues_at))]
        return element(ID["seek_head"], b"".join(seeks))
    info_at = len(seek_head(0, 0, 0))
    cluster_at = info_at + len(info) + len(tracks)
    cluster = element(CLUSTER, b"".join(blocks))
    points = [element(ID["cue_point"], number(ID["cue_time"], milliseconds) + element(ID["cue_track_positions"], number(ID["cue_track"], 1, 1)
              + number(ID["cue_cluster_position"], cluster_at, 8) + number(ID["cue_relative_position"], offsets[block], 4))) for milliseconds, block in cues]
    body = seek_head(info_at, info_at + len(info), cluster_at + len(cluster)) + info + tracks + cluster + element(ID["cues"], b"".join(points))
    path.write_bytes(element(ID["segment"], body))
    return str(path)


def test_cue_points_aliasing_one_block_read_it_once(tmp_path):
    video = write_matroska(tmp_path / "alias.mkv", [simple_block(b"A" * 100_000), simple_block(b"second")], [(5000, 0)] * 40 + [(1000, 0), (7000, 1)])
    assert matroska.matroska_texts(video, 1, 0) == [(1.0, 3.0, "A" * 100_000), (7.0, 9.0, "second")]


def test_too_many_subtitle_bytes_or_cue_points_raise_so_the_caller_falls_back(tmp_path, monkeypatch):
    video = write_matroska(tmp_path / "large.mkv", [simple_block(b"A" * 600), simple_block(b"B" * 600)], [(1000, 0), (2000, 1)])
    monkeypatch.setattr(matroska, "MAX_SUBTITLE_BYTES", 1000)
    with pytest.raises(MatroskaError, match="larger than 1000"):
        matroska.matroska_texts(video, 1, 0)
    monkeypatch.setattr(matroska, "MAX_CUE_POINTS", 1)
    with pytest.raises(MatroskaError, match="more than 1 cue points"):
        matroska.matroska_cue_timings(video, 1, [])


def test_bitmap_like_tracks_get_the_size_of_each_simple_or_grouped_block(tmp_path):
    video = write_matroska(tmp_path / "bitmap.mkv", [simple_block(b"x" * 300), group_block(b"y" * 50)], [(1000, 0), (4000, 1)])
    assert matroska.matroska_cue_timings(video, 1, [0]) == {0: [(1.0, None, 304), (4.0, None, 54)]}


def test_review_lines_fall_back_to_ffmpeg_when_the_index_gives_no_text(tmp_path, monkeypatch):
    video, starts, _ = video_with_reference(tmp_path, minutes=2)
    indexed = matroska.matroska_texts(str(video), 2, 1)
    monkeypatch.setattr(review, "matroska_texts", lambda *arguments, **options: None)
    extracted = review.reference_texts(str(video), {"image": False, "index": 1})
    assert len(extracted) == len(starts) and [text for _, _, text in extracted] == [text for _, _, text in indexed]
    assert all(abs(start - indexed_start) < 0.01 for (start, _, _), (indexed_start, _, _) in zip(extracted, indexed, strict=True))
