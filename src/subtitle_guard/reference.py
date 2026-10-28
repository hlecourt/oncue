"""Find the embedded subtitle track whose timing is the reference for a video, and cache it."""

import hashlib
import json
import os
import subprocess
import time

import numpy

from .alignment import MIN_CUES
from .logs import LEVEL, log
from .matroska import matroska_cue_timings
from .storage import heavy_disk_read, state_path, write_json


REFERENCE_CACHE_VERSION = 4
TEXT_CODECS = ("subrip", "ass", "ssa", "mov_text", "webvtt", "text")
CUE_PER_PACKET_BITMAP_CODECS = ("dvd_subtitle",)
SPLIT_BITMAP_CODECS = ("hdmv_pgs_subtitle",)
BITMAP_MIN_PACKET_BYTES = 100
EMPTY_TEXT_PACKET_BYTES = 2


def load_reference(video, hearing_impaired=False, subtitle_cue_count=0):
    return choose_reference(load_tracks(video), hearing_impaired, subtitle_cue_count)


def load_tracks(video):
    stat = os.stat(video)
    cache_key = hashlib.sha1(f"{REFERENCE_CACHE_VERSION}|{video}|{stat.st_size}|{int(stat.st_mtime)}".encode()).hexdigest()
    cache_file = state_path("references", f"{cache_key}.json")
    if os.path.exists(cache_file):
        try:
            return json.load(open(cache_file))
        except ValueError as error:
            log("reference.cache-corrupt", f"track cache unreadable, re-extracting: {error}", LEVEL["warning"], video=video, cache=cache_file)
    started = time.time()
    tracks = extract_tracks(video)
    write_json(cache_file, tracks)
    log("reference.extracted", f"{len(tracks)} candidate tracks extracted in {time.time() - started:.2f}s", LEVEL["debug"], video=video,
        seconds=round(time.time() - started, 2), tracks=[{key: track[key] for key in ("index", "language", "image", "sdh")} | {"lines": len(track["starts"])}
                                                        for track in tracks])
    return tracks


def choose_reference(tracks, hearing_impaired, subtitle_cue_count):
    complete = [track for track in tracks if len(track["starts"]) >= max(MIN_CUES, subtitle_cue_count / 2)]
    if not complete:
        return None
    best = min(complete, key=lambda track: (track["image"], track["sdh"] != hearing_impaired, track["language"] not in ("en", "eng"),
                                            abs(numpy.log(len(track["starts"]) / max(subtitle_cue_count, 1)))))
    kind = "image" if best["image"] else ("text-sdh" if best["sdh"] else "text")
    return {"kind": f"{best['language']}-{kind}", "starts": numpy.array(best["starts"]), "ends": numpy.array(best["ends"]), "index": best["index"],
            "image": best["image"]}


def extract_tracks(video):
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=index,codec_type,codec_name:stream_tags=language,title:stream_disposition=forced,hearing_impaired",
                            "-of", "json", video], capture_output=True, text=True)
    streams = json.loads(probe.stdout or "{}").get("streams", [])
    candidates = [stream for stream in streams if stream.get("codec_type") == "subtitle" and is_reference_candidate(stream)]
    text_candidates = [stream for stream in candidates if stream.get("codec_name") in TEXT_CODECS]
    candidates = text_candidates or candidates
    if not candidates:
        return []
    try:
        track_count = sum(stream.get("codec_type") != "attachment" for stream in streams)
        indexed = matroska_cue_timings(video, track_count, [stream["index"] for stream in candidates if stream.get("codec_name") in SPLIT_BITMAP_CODECS])
    except (ValueError, IndexError, KeyError, StopIteration, OSError) as error:
        log("reference.index-unavailable", f"Matroska Cues unusable ({type(error).__name__}: {error}), falling back to a packet scan", LEVEL["debug"],
            video=video)
        indexed = {}
    missing = [stream["index"] for stream in candidates if stream["index"] not in indexed]
    if missing:
        log("reference.packet-scan", f"ffprobe packet scan for streams {missing}", LEVEL["debug"], video=video, tracks=missing)
    packets = packet_timings(video) if missing else {}
    tracks = []
    for stream in candidates:
        entries = indexed.get(stream["index"]) or packets.get(stream["index"], [])
        starts, ends = cue_times(stream.get("codec_name"), entries)
        tags = stream.get("tags", {})
        tracks.append({"index": stream["index"], "language": tags.get("language", "und").lower(), "image": stream.get("codec_name") not in TEXT_CODECS,
                       "sdh": bool(stream.get("disposition", {}).get("hearing_impaired") or "sdh" in tags.get("title", "").lower()),
                       "starts": starts, "ends": ends})
    return tracks


def is_reference_candidate(stream):
    title = stream.get("tags", {}).get("title", "").lower()
    if stream.get("disposition", {}).get("forced") or "forced" in title or "comment" in title:
        return False
    return stream.get("codec_name") in TEXT_CODECS + CUE_PER_PACKET_BITMAP_CODECS + SPLIT_BITMAP_CODECS


def cue_times(codec, entries):
    if codec in SPLIT_BITMAP_CODECS:
        starts = sorted(start for start, _, size in entries if size is not None and size >= BITMAP_MIN_PACKET_BYTES)
        ends = sorted(start for start, _, size in entries if size is not None and size < BITMAP_MIN_PACKET_BYTES)
        return starts, ends
    shown = [(start, duration) for start, duration, size in entries if size is None or size > EMPTY_TEXT_PACKET_BYTES]
    return sorted(start for start, _ in shown), sorted(start + duration for start, duration in shown if duration)


def packet_timings(video):
    with heavy_disk_read():
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "packet=stream_index,pts_time,duration_time,size",
                                "-of", "json", video], capture_output=True, text=True)
    timings = {}
    for packet in json.loads(probe.stdout or "{}").get("packets", []):
        if packet.get("pts_time") in (None, "N/A"):
            continue
        duration = float(packet["duration_time"]) if packet.get("duration_time") not in (None, "N/A") else None
        timings.setdefault(packet["stream_index"], []).append((float(packet["pts_time"]), duration, int(packet.get("size", 0))))
    return timings
