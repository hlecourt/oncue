"""Read and write SRT, WebVTT and ASS/SSA subtitles in any common encoding."""

import os
import re


TIMING = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[,.]\d{1,3})")
HEARING_IMPAIRED_TAGS = (".hi.", ".sdh.", ".cc.")


def parse_subtitle(path):
    text = decode(open(path, "rb").read()).replace("\r\n", "\n").replace("\r", "\n")
    lowered = text.lower()
    if "[events]" in lowered and "dialogue:" in lowered:
        return parse_ass(text)
    subtitle_format = "vtt" if text.lstrip("﻿ \n").startswith("WEBVTT") else "srt"
    return {"format": subtitle_format, "cues": parse_timed_text(text)}


def decode(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def parse_timed_text(text):
    cues, current = [], None
    for line in text.split("\n"):
        timing = TIMING.match(line)
        if timing:
            if current:
                cues.append(finish_cue(current, followed_by_timing=True))
            current = (clock(timing.group(1)), clock(timing.group(2)), [])
            continue
        if current is None:
            continue
        if not line.strip():
            cues.append(finish_cue(current, followed_by_timing=False))
            current = None
            continue
        current[2].append(line.rstrip())
    if current:
        cues.append(finish_cue(current, followed_by_timing=False))
    return [cue for cue in cues if cue[2]]


def finish_cue(current, followed_by_timing):
    start, end, lines = current
    if followed_by_timing and lines and lines[-1].strip().isdigit():
        lines = lines[:-1]
    return start, end, "\n".join(lines).strip()


def parse_ass(text):
    lines, cues, events, columns, in_events = text.split("\n"), [], [], None, False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.lower() == "[events]"
            continue
        if not in_events:
            continue
        if stripped.lower().startswith("format:"):
            columns = [column.strip().lower() for column in stripped.split(":", 1)[1].split(",")]
            continue
        if columns is None or not stripped.lower().startswith("dialogue:") or not {"start", "end", "text"} <= set(columns):
            continue
        fields = line.split(":", 1)[1].split(",", len(columns) - 1)
        if len(fields) != len(columns):
            continue
        cues.append((clock(fields[columns.index("start")].strip()), clock(fields[columns.index("end")].strip()), fields[-1].strip()))
        events.append(index)
    return {"format": "ass", "cues": cues, "lines": lines, "events": events, "columns": columns}


def clock(value):
    *larger, whole = value.replace(",", ".").split(":")
    total = float(whole)
    for unit, part in zip((60, 3600), reversed(larger)):
        total += unit * int(part)
    return total


def write_subtitle(path, document, timings):
    if document["format"] == "ass":
        content = ass_content(document, timings)
    else:
        placed = sorted(timings, key=lambda timing: timing[0])
        separator = "." if document["format"] == "vtt" else ","
        blocks = [f"{number}\n{timestamp(start, separator)} --> {timestamp(end, separator)}\n{text}\n" for number, (start, end, text) in enumerate(placed, 1)]
        content = ("WEBVTT\n\n" if document["format"] == "vtt" else "") + "\n".join(blocks)
    temporary = path + ".subtitle-guard.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(temporary, path)


def ass_content(document, timings):
    lines, columns = list(document["lines"]), document["columns"]
    for line_index, timing in zip(document["events"], timings):
        prefix, body = lines[line_index].split(":", 1)
        fields = body.split(",", len(columns) - 1)
        fields[columns.index("start")] = ass_timestamp(timing[0])
        fields[columns.index("end")] = ass_timestamp(timing[1])
        lines[line_index] = prefix + ":" + ",".join(fields)
    return "\n".join(lines)


def timestamp(value, separator=","):
    milliseconds = max(0, round(value * 1000))
    return f"{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d}{separator}{milliseconds % 1000:03d}"


def ass_timestamp(value):
    centiseconds = max(0, round(value * 100))
    return f"{centiseconds // 360000}:{centiseconds // 6000 % 60:02d}:{centiseconds // 100 % 60:02d}.{centiseconds % 100:02d}"
