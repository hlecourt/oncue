# SPDX-License-Identifier: GPL-3.0-or-later
"""The text of the reference track, for the line list of the editor and the text check: read from a text track, from an SRT file prepared
offline for a picture track, or, when Tesseract is installed, read from the pictures themselves. Cached per video and track."""

import glob
import hashlib
import os
import shutil
import tempfile

from .errors import MatroskaError, ToolError
from .logs import LEVEL, log
from .matroska import matroska_texts
from .reference import MEDIA_INPUT_OPTIONS, paired_cues, probe_json, run_on_video
from .storage import heavy_disk_read, read_json, state_path, write_json
from .subtitles import parse_timed_text
from .tools import EXTRACT_SECONDS, run_tool, stderr_tail

OFFLINE_FOLDER = "reference-texts"
OCR_FRAMES_PER_SECOND = 2
OCR_SECONDS = 20
OCR_CACHE_VERSION = 1
TESSERACT_LANGUAGES = {"en": "eng", "eng": "eng", "fr": "fra", "fre": "fra", "fra": "fra", "de": "deu", "ger": "deu", "deu": "deu", "es": "spa",
                       "spa": "spa", "it": "ita", "ita": "ita", "pt": "por", "por": "por", "nl": "nld", "dut": "nld", "nld": "nld"}


def reference_texts(video, reference, read_pictures=False):
    if reference["image"]:
        return offline_texts(video, reference["index"]) or (picture_texts(video, reference) if read_pictures else None)
    status = os.stat(video)
    cache_file = state_path("texts", hashlib.sha1(f"{video}|{status.st_size}|{int(status.st_mtime)}|{reference['index']}".encode()).hexdigest() + ".json")
    if os.path.exists(cache_file):
        return [tuple(line) for line in read_json(cache_file)]
    try:
        probe = probe_json(["ffprobe", "-v", "error", *MEDIA_INPUT_OPTIONS, "-show_entries", "stream=index,codec_type,codec_name", "-of", "json", video],
                           video).get("streams", [])
    except ToolError as error:
        log("review.reference-unreadable", f"video streams unreadable for the line list: {error}", LEVEL["warning"], video=video)
        return None
    codec = next((stream.get("codec_name") for stream in probe if stream["index"] == reference["index"]), "")
    try:
        texts = matroska_texts(video, sum(stream.get("codec_type") != "attachment" for stream in probe), reference["index"], ass=codec in ("ass", "ssa"))
    except (MatroskaError, OSError):
        texts = None
    if texts is None:
        try:
            with heavy_disk_read():
                extracted = run_on_video(["ffmpeg", "-nostdin", "-loglevel", "error", *MEDIA_INPUT_OPTIONS, "-i", video, "-map", f"0:{reference['index']}",
                                          "-f", "srt", "-"], video, EXTRACT_SECONDS)
        except ToolError as error:
            log("review.reference-unreadable", f"reference text not extracted for the line list: {error}", LEVEL["warning"], video=video)
            return None
        if extracted.returncode != 0:
            log("review.reference-unreadable", f"reference text not extracted for the line list: {stderr_tail(extracted)}", LEVEL["warning"], video=video)
            return None
        texts = parse_timed_text(extracted.stdout.replace("\r\n", "\n"))
    write_json(cache_file, texts)
    return texts


def offline_texts(video, index):
    # an SRT of a picture track read elsewhere (for example by a desktop OCR tool), named after the video and the track index
    path = os.path.join(state_path(OFFLINE_FOLDER), f"{os.path.basename(video)}.{index}.srt")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as handle:
        return parse_timed_text(handle.read().replace("\r\n", "\n"))


def picture_texts(video, reference):
    if not shutil.which("tesseract"):
        return None
    status = os.stat(video)
    cache_file = state_path("texts", hashlib.sha1(f"ocr{OCR_CACHE_VERSION}|{video}|{status.st_size}|{int(status.st_mtime)}|{reference['index']}".encode())
                            .hexdigest() + ".json")
    if os.path.exists(cache_file):
        return [tuple(line) for line in read_json(cache_file)]
    pairs = paired_cues(reference["starts"], reference["ends"])
    if not pairs:
        return None
    with tempfile.TemporaryDirectory(prefix="oncue-ocr-") as folder:
        frames = render_pictures(video, reference["index"], pairs[-1][1] + 1, folder)
        if frames is None:
            return None
        texts = []
        for start, end in pairs:
            frame = frames.get(round((start + end) / 2 * OCR_FRAMES_PER_SECOND))
            text = read_picture(frame, TESSERACT_LANGUAGES.get(reference["kind"].split("-")[0], "eng")) if frame else ""
            if text:
                texts.append((start, end, text))
    log("reference.pictures-read", f"{len(texts)} of {len(pairs)} picture subtitles read by OCR", video=video, track=reference["index"])
    write_json(cache_file, texts)
    return texts


def render_pictures(video, index, duration, folder):
    # the picture track drawn alone on black, a few frames a second; frame n shows the subtitle on screen at n / OCR_FRAMES_PER_SECOND
    command = ["ffmpeg", "-nostdin", "-loglevel", "error", *MEDIA_INPUT_OPTIONS, "-i", video, "-f", "lavfi", "-i",
               f"color=black:s=1920x1080:r={OCR_FRAMES_PER_SECOND}:d={duration:.0f}", "-filter_complex",
               f"[1:v][0:{index}]overlay=eof_action=pass,crop=iw:ih*0.3:0:ih*0.7,format=gray", os.path.join(folder, "%06d.png")]
    try:
        with heavy_disk_read():
            result = run_on_video(command, video, EXTRACT_SECONDS)
    except ToolError as error:
        log("reference.pictures-unreadable", f"picture subtitles not rendered for OCR: {error}", LEVEL["warning"], video=video)
        return None
    if result.returncode != 0:
        log("reference.pictures-unreadable", f"picture subtitles not rendered for OCR: {stderr_tail(result)}", LEVEL["warning"], video=video)
        return None
    return {int(os.path.basename(path)[:-4]) - 1: path for path in glob.glob(os.path.join(folder, "*.png"))}


def read_picture(path, language):
    try:
        result = run_tool(["tesseract", path, "stdout", "--psm", "6", "-l", language], OCR_SECONDS)
    except ToolError:
        return ""
    return " ".join(result.stdout.split()) if result.returncode == 0 else ""
