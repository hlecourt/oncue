# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from oncue import state
from oncue.guard import evaluate_and_fix
from oncue.subtitles import ass_timestamp, parse_subtitle, write_subtitle

SRT = ("1\r\n00:00:01,000 --> 00:00:02,000\r\nBonjour\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n\r\n"
       "3\r\n00:00:05,500 --> 00:00:06,000\r\nDeux\r\nlignes\r\n4\r\n00:00:07,000 --> 00:00:08,000\r\nSans blanc avant\r\n")
ASS = ("[Script Info]\nTitle: test\n\n[V4+ Styles]\nFormat: Name, Fontname\nStyle: Default,Arial\n\n[Events]\n"
       "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
       "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Bonjour, toi {\\i1}ici{\\i0}\n"
       "Comment: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,note\n"
       "Dialogue: 1,0:01:05.25,0:01:06.00,Sign,,0,0,0,,{\\pos(10,20)}Panneau\n")


def write(folder, name, content, encoding="utf-8"):
    path = folder / name
    path.write_text(content, encoding=encoding, newline="")
    return str(path)


def test_srt_drops_empty_cues_and_keeps_the_next_ones(tmp_path):
    cues = parse_subtitle(write(tmp_path, "basic.srt", SRT))["cues"]
    assert [start for start, _, _ in cues] == [1.0, 5.5, 7.0]
    assert cues[1][2] == "Deux\nlignes"


def test_srt_encodings(tmp_path):
    assert parse_subtitle(write(tmp_path, "utf16.srt", "﻿" + SRT, "utf-16"))["cues"][0][2] == "Bonjour"
    assert parse_subtitle(write(tmp_path, "cp1252.srt", "1\n00:00:01,000 --> 00:00:02,000\nÇa été\n", "cp1252"))["cues"][0][2] == "Ça été"


def test_srt_rewrite_changes_only_the_timings_and_keeps_the_order(tmp_path):
    path = write(tmp_path, "unsorted.srt", "1\n00:00:09,000 --> 00:00:10,000\nB\n\n2\n00:00:01,000 --> 00:00:02,000\nA\n")
    document = parse_subtitle(path)
    write_subtitle(path, document, [(start + 1, end + 1, text) for start, end, text in document["cues"]])
    assert Path(path).read_text() == "1\n00:00:10,000 --> 00:00:11,000\nB\n\n2\n00:00:02,000 --> 00:00:03,000\nA\n"


def test_a_rewrite_keeps_the_encoding_line_endings_and_bom(tmp_path):
    russian = tmp_path / "ru.srt"
    russian.write_bytes("1\r\n00:00:01,000 --> 00:00:02,000\r\nПривет мир\r\n".encode("cp1251"))
    document = parse_subtitle(str(russian))
    write_subtitle(str(russian), document, [(start + 2, end + 2, text) for start, end, text in document["cues"]])
    assert russian.read_bytes() == "1\r\n00:00:03,000 --> 00:00:04,000\r\nПривет мир\r\n".encode("cp1251")
    bom = tmp_path / "bom.srt"
    bom.write_bytes(b"\xef\xbb\xbf1\n00:00:01,000 --> 00:00:02,000\nCa va\n")
    document = parse_subtitle(str(bom))
    write_subtitle(str(bom), document, document["cues"])
    assert bom.read_bytes().startswith(b"\xef\xbb\xbf1\n")


def test_a_vtt_rewrite_keeps_settings_styles_notes_and_lines_after_a_blank(tmp_path):
    content = ("WEBVTT\n\nSTYLE\n::cue { color: yellow }\n\nNOTE kept\n\nintro\n00:00:01.000 --> 00:00:02.000 line:0 align:start\nHaut\n\n"
               "00:00:03.000 --> 00:00:04.000\nPremier\n\nencore\n")
    path = write(tmp_path, "styled.vtt", content)
    document = parse_subtitle(path)
    write_subtitle(path, document, [(start + 1, end + 1, text) for start, end, text in document["cues"]])
    assert Path(path).read_text() == content.replace("00:00:01.000 --> 00:00:02.000", "00:00:02.000 --> 00:00:03.000").replace(
        "00:00:03.000 --> 00:00:04.000", "00:00:04.000 --> 00:00:05.000")


def test_vtt_without_hours_round_trip(tmp_path):
    path = write(tmp_path, "short.vtt", "WEBVTT\n\nNOTE a comment\n\nintro\n01:02.500 --> 01:04.000 align:start\nSans heures\n\n"
                                        "00:01:05.000 --> 00:01:06.000\nAvec heures\n")
    document = parse_subtitle(path)
    assert document["format"] == "vtt"
    assert [start for start, _, _ in document["cues"]] == [62.5, 65.0]
    write_subtitle(path, document, [(start + 0.25, end + 0.25, text) for start, end, text in document["cues"]])
    assert Path(path).read_text().startswith("WEBVTT\n\nNOTE a comment\n\nintro\n00:01:02.750 --> 00:01:04.250 align:start\nSans heures")


def test_ass_only_times_change(tmp_path):
    path = write(tmp_path, "test.ass", ASS)
    document = parse_subtitle(path)
    assert [(start, text) for start, _, text in document["cues"]] == [(1.0, "Bonjour, toi {\\i1}ici{\\i0}"), (65.25, "{\\pos(10,20)}Panneau")]
    write_subtitle(path, document, [(start + 2, end + 2, text) for start, end, text in document["cues"]])
    expected = ASS.replace("0:00:01.00,0:00:02.50", "0:00:03.00,0:00:04.50").replace("0:01:05.25,0:01:06.00", "0:01:07.25,0:01:08.00")
    assert Path(path).read_text() == expected
    assert ass_timestamp(3725.456) == "1:02:05.46"


def test_forced_and_short_files_are_left_alone_before_reading_the_video(tmp_path):
    assert evaluate_and_fix("/nonexistent.mkv", write(tmp_path, "x.fr.forced.srt", SRT), False)["decision"] == "unverified"
    assert evaluate_and_fix("/nonexistent.mkv", write(tmp_path, "x.fr.srt", SRT), False)["decision"] == "unverified"


def test_state_is_kept_per_language():
    video = "/media/Show/Season 1/Show.S01E01.mkv"
    french, english = state.state_file(video, "/media/Show/Season 1/Show.S01E01.fr.srt"), state.state_file(video, "/media/Show/Season 1/Show.S01E01.en.srt")
    assert french != english



def test_utf16_big_endian_with_crlf_comes_back_byte_for_byte(tmp_path):
    path = tmp_path / "be.srt"
    original = "1\r\n00:00:01,000 --> 00:00:02,000\r\nBonjour\r\n"
    path.write_bytes(b"\xfe\xff" + original.encode("utf-16-be"))
    document = parse_subtitle(str(path))
    write_subtitle(str(path), document, document["cues"])
    assert path.read_bytes() == b"\xfe\xff" + original.encode("utf-16-be")


def test_impossible_sizes_and_times_are_not_checked(tmp_path):
    from oncue.alignment import MAX_CHECKED_CUES, oversized_reason
    assert oversized_reason([(1.0, 2.0, "x")] * (MAX_CHECKED_CUES + 1)).startswith("too many lines")
    assert oversized_reason([(1.0, 9e9, "x")]).startswith("times beyond")
    assert oversized_reason([(1.0, 2.0, "x")]) is None
