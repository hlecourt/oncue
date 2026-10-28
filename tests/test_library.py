import os
import shutil
import subprocess

import numpy
import pytest
from synthetic import dialogue

from subtitle_guard import jobs, library
from subtitle_guard.subtitles import parse_subtitle, timestamp


def touch(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(content)


def test_finds_external_subtitles_and_episode_neighbours(tmp_path):
    season = tmp_path / "Show" / "Season 1"
    for number in range(1, 6):
        touch(str(season / f"Show.S01E{number:02d}.1080p.mkv"))
    touch(str(season / "Show.S01E03.1080p.fr.srt"))
    touch(str(season / "Show.S01E03.1080p.en.hi.ass"))
    touch(str(season / "Show.S01E03.1080p.fr.srt.subtitle-guard.tmp"))
    touch(str(season / "Show.S01E03.1080p-sample.mkv"))
    touch(str(tmp_path / ".trash" / "Old.S01E01.mkv"))
    touch(str(tmp_path / ".trash" / "Old.S01E01.fr.srt"))
    items = list(library.library_items(str(tmp_path)))
    assert [os.path.basename(subtitle) for _, subtitle, _ in items] == ["Show.S01E03.1080p.en.hi.ass", "Show.S01E03.1080p.fr.srt"]
    assert [label for label, _ in items[0][2]] == ["S01E01", "S01E02", "S01E04", "S01E05"]


def test_episode_keys():
    assert library.episode_key("Show.S02E09.mkv") == (2, 9)
    assert library.episode_key("show 3x12 title.mkv") == (3, 12)
    assert library.episode_key("Movie.2023.1080p.mkv") is None


def test_watch_queues_new_and_changed_subtitles_once(tmp_path, monkeypatch):
    touch(str(tmp_path / "Movie (2020)" / "Movie.2020.mkv"))
    subtitle = tmp_path / "Movie (2020)" / "Movie.2020.fr.srt"
    touch(str(subtitle), "1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
    rounds = []

    def stop_after_two_rounds(seconds):
        rounds.append(seconds)
        if len(rounds) == 1:
            touch(str(subtitle), "1\n00:00:01,000 --> 00:00:02,500\nBonjour !\n")
            os.utime(subtitle, (os.stat(subtitle).st_atime, os.stat(subtitle).st_mtime + 5))
            return
        if len(rounds) == 2:
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(library.time, "sleep", stop_after_two_rounds)
    with pytest.raises(KeyboardInterrupt):
        library.watch([str(tmp_path)], apply=False)
    assert [item["arguments"]["subtitle"] for item in jobs.recent()] == [str(subtitle), str(subtitle)]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_scan_folder_fixes_a_misaligned_subtitle(tmp_path, capsys):
    starts, ends = dialogue(6, minutes=6)
    lines = "\n".join(f"{number}\n{timestamp(start)} --> {timestamp(end)}\nline {number}\n" for number, (start, end) in enumerate(zip(starts, ends, strict=False), 1))
    (tmp_path / "reference.srt").write_text(lines)
    video = tmp_path / "library" / "Movie (2020)" / "Movie.2020.mkv"
    video.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=size=64x64:rate=25:duration={int(ends[-1]) + 5}",
                    "-i", str(tmp_path / "reference.srt"), "-map", "0", "-map", "1", "-c:v", "mpeg4", "-c:s", "srt", "-metadata:s:s:0", "language=eng",
                    str(video)], check=True)
    subtitle = video.with_suffix(".fr.srt")
    subtitle.write_text("\n".join(f"{number}\n{timestamp(start * 23.976 / 25 - 4)} --> {timestamp(end * 23.976 / 25 - 4)}\nligne {number}\n"
                                  for number, (start, end) in enumerate(zip(starts, ends, strict=False), 1) if start * 23.976 / 25 > 4))
    library.scan_folder(str(tmp_path / "library"), apply=True)
    assert "fixed" in capsys.readouterr().out
    fixed = numpy.array([start for start, _, _ in parse_subtitle(str(subtitle))["cues"]])
    expected = starts[starts * 23.976 / 25 > 4]
    assert numpy.max(numpy.abs(fixed - expected)) < 0.31


def test_the_watcher_queues_once_remembers_after_success_and_retries_failures_later(tmp_path, monkeypatch):
    from subtitle_guard import jobs, library, worker
    video, subtitle = tmp_path / "Movie.mkv", tmp_path / "Movie.fr.srt"
    video.write_bytes(b"v")
    subtitle.write_text("s")
    assert library.queue_if_changed(str(video), str(subtitle), [], True) == 1
    assert library.queue_if_changed(str(video), str(subtitle), [], True) == 0
    monkeypatch.setattr(worker, "evaluate_and_fix", lambda video, subtitle, apply, neighbours: {"decision": "kept"})
    worker.work_once(1)
    assert jobs.seen(str(subtitle))[0] == library.watch_signature(str(video), str(subtitle), True)
    assert library.queue_if_changed(str(video), str(subtitle), [], True) == 0
    assert library.queue_if_changed(str(video), str(subtitle), [], False) == 1

    def broken(video, subtitle, apply, neighbours):
        raise OSError("disk gone")
    monkeypatch.setattr(worker, "evaluate_and_fix", broken)
    worker.work_once(1)
    assert jobs.seen(str(subtitle))[0].startswith(library.FAILED)
    assert library.queue_if_changed(str(video), str(subtitle), [], False) == 0
    monkeypatch.setattr(library, "FAILED_RETRY_SECONDS", -1)
    assert library.queue_if_changed(str(video), str(subtitle), [], False) == 1
    subtitle.unlink()
    assert library.queue_if_changed(str(video), str(subtitle), [], False) == 0


def test_wrong_end_times_are_rejected(tmp_path):
    from subtitle_guard.guard import evaluate_and_fix
    from subtitle_guard.subtitles import timestamp
    subtitle = tmp_path / "late-ends.fr.srt"
    subtitle.write_text("".join(f"{number}\n{timestamp(number * 4.0)} --> {timestamp(number * 4.0 + 600)}\nline {number}\n\n" for number in range(1, 150)))
    outcome = evaluate_and_fix("/missing.mkv", str(subtitle), True)
    assert outcome["decision"] == "rejected" and "end times are wrong" in outcome["reason"]
