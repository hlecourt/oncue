import os
import shutil
import subprocess

import pytest

from subtitle_guard import reference

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


def make_video(folder, extension, codec):
    subtitle = folder / "reference.srt"
    subtitle.write_text("".join(f"{number}\n00:00:{number:02d},500 --> 00:00:{number:02d},900\nline {number}\n\n" for number in range(1, 56)))
    video = folder / f"video.{extension}"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=size=64x64:rate=25:duration=60",
                    "-i", str(subtitle), "-map", "0", "-map", "1", "-c:v", "mpeg4", "-c:s", codec, "-metadata:s:s:0", "language=eng", str(video)],
                   check=True)
    return str(video)


@needs_ffmpeg
@pytest.mark.parametrize("extension, codec", [("mkv", "srt"), ("mp4", "mov_text")])
def test_embedded_text_track_gives_start_and_end_times(tmp_path, extension, codec):
    tracks = reference.extract_tracks(make_video(tmp_path, extension, codec))
    assert len(tracks) == 1
    assert tracks[0]["language"] == "eng"
    assert tracks[0]["starts"] == pytest.approx([number + 0.5 for number in range(1, 56)], abs=0.002)
    assert tracks[0]["ends"] == pytest.approx([number + 0.9 for number in range(1, 56)], abs=0.002)


def test_forced_and_commentary_tracks_are_not_references():
    assert not reference.is_reference_candidate({"codec_name": "subrip", "disposition": {"forced": 1}})
    assert not reference.is_reference_candidate({"codec_name": "subrip", "tags": {"title": "Director Commentary"}})
    assert reference.is_reference_candidate({"codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"}})


def test_track_choice_prefers_complete_text_tracks_then_english():
    short_english = {"index": 2, "language": "eng", "image": False, "sdh": False, "starts": list(range(60)), "ends": []}
    full_french = {"index": 3, "language": "fre", "image": False, "sdh": False, "starts": list(range(600)), "ends": []}
    full_english_image = {"index": 4, "language": "eng", "image": True, "sdh": False, "starts": list(range(600)), "ends": []}
    chosen = reference.choose_reference([short_english, full_french, full_english_image], False, 550)
    assert chosen["kind"] == "fre-text" and chosen["index"] == 3 and not chosen["image"]


def test_a_failed_probe_is_an_error_and_is_never_cached(tmp_path, monkeypatch):
    from subtitle_guard import storage, tools
    from subtitle_guard.errors import ProbeError
    from subtitle_guard.guard import evaluate_and_fix
    video, subtitle = tmp_path / "movie.mkv", tmp_path / "movie.fr.srt"
    video.write_bytes(b"not a video")
    subtitle.write_text("".join(f"{number}\n00:{number // 60:02d}:{number % 60:02d},000 --> 00:{number // 60:02d}:{number % 60:02d},500\nline\n\n"
                                for number in range(1, 120)))
    monkeypatch.setattr(reference, "run_tool", lambda command, timeout: tools.subprocess.CompletedProcess(command, 1, "", "Input/output error"))
    with pytest.raises(ProbeError):
        reference.load_tracks(str(video))
    assert not os.listdir(storage.state_path("references")) if os.path.isdir(storage.state_path("references")) else True
    outcome = evaluate_and_fix(str(video), str(subtitle), True)
    assert outcome["decision"] == "unverified" and "could not read the video" in outcome["reason"] and "Input/output error" in outcome["reason"]


def test_a_complete_bitmap_track_is_kept_when_the_text_track_is_short(monkeypatch):
    streams = [{"index": 2, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}},
               {"index": 3, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"}}]
    monkeypatch.setattr(reference, "probe_json", lambda command, video: {"streams": streams})
    monkeypatch.setattr(reference, "read_tracks", lambda video, all_streams, candidates: [
        {"index": stream["index"], "language": "eng", "image": stream["codec_name"] != "subrip", "sdh": False,
         "starts": list(range(40 if stream["codec_name"] == "subrip" else 800)), "ends": []} for stream in candidates])
    tracks = reference.extract_tracks("/movie.mkv")
    assert [track["index"] for track in tracks] == [2, 3]
    assert reference.choose_reference(tracks, False, 780)["index"] == 3


def test_a_hung_tool_raises_a_typed_error():
    from subtitle_guard import tools
    from subtitle_guard.errors import ToolError
    with pytest.raises(ToolError):
        tools.run_tool(["sleep", "5"], 0.2)


def test_an_oversized_matroska_element_is_refused(tmp_path):
    from subtitle_guard import matroska
    from subtitle_guard.errors import MatroskaError
    path = tmp_path / "huge.bin"
    path.write_bytes(bytes([0x1A, 0x45, 0xDF, 0xA3, 0x01, 0x7F, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
    with open(path, "rb") as handle, pytest.raises(MatroskaError):
        matroska.read_element(handle, 0)
