import shutil
import subprocess

import pytest

from subtitle_guard import reference

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


def make_video(folder, extension, codec):
    subtitle = folder / "reference.srt"
    subtitle.write_text("".join(f"{number}\n00:00:{number:02d},500 --> 00:00:{number:02d},900\nline {number}\n\n" for number in range(1, 56)))
    video = folder / f"video.{extension}"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=size=64x64:rate=25:duration=60",
                    "-i", str(subtitle), "-map", "0", "-map", "1", "-c:v", "mpeg4", "-c:s", codec, "-metadata:s:s:0", "language=eng", str(video)],
                   check=True)
    return str(video)


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
