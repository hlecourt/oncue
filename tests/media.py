# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a small video with an embedded English text track from synthetic dialogue, for end-to-end tests."""

import subprocess

from synthetic import dialogue

from subtitle_guard.subtitles import timestamp


def video_with_reference(folder, name="Movie (2020)/Movie.2020.mkv", seed=8, minutes=6):
    starts, ends = dialogue(seed, minutes=minutes)
    folder.mkdir(parents=True, exist_ok=True)
    reference = folder / "reference.srt"
    reference.write_text("\n".join(f"{number}\n{timestamp(start)} --> {timestamp(end)}\nline {number}\n" for number, (start, end) in enumerate(zip(starts, ends, strict=False), 1)))
    video = folder / "library" / name
    video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=size=64x64:rate=25:duration={int(ends[-1]) + 5}",
                    "-i", str(reference), "-map", "0", "-map", "1", "-c:v", "mpeg4", "-c:s", "srt", "-metadata:s:s:0", "language=eng", str(video)], check=True)
    return video, starts, ends


def write_subtitle_file(path, starts, ends, shift):
    path.write_text("\n".join(f"{number}\n{timestamp(shift(start))} --> {timestamp(shift(end))}\nligne {number}\n"
                              for number, (start, end) in enumerate(zip(starts, ends, strict=False), 1)))
