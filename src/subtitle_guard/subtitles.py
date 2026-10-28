# SPDX-License-Identifier: GPL-3.0-or-later
"""Read and write SRT, WebVTT and ASS/SSA subtitles in any common encoding."""

import codecs
import os
import re

from .files import read_regular, replace_file

TIMING = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[,.]\d{1,3})")
HEARING_IMPAIRED_TAGS = (".hi.", ".sdh.", ".cc.")


def is_hearing_impaired(subtitle):
    return any(tag in os.path.basename(subtitle).lower() for tag in HEARING_IMPAIRED_TAGS)


def parse_subtitle(path):
    raw = read_regular(path)
    text, encoding = decode(raw)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = {"lines": lines, "encoding": encoding, "newline": "\r\n" if b"\r\n" in raw else "\n"}
    lowered = text.lower()
    if "[events]" in lowered and "dialogue:" in lowered:
        return {**parse_ass(lines), **kept}
    cues, timing_lines = parse_timed_lines(lines)
    subtitle_format = "vtt" if text.lstrip("\ufeff \n").startswith("WEBVTT") else "srt"
    return {"format": subtitle_format, "cues": cues, "timing_lines": timing_lines, **kept}


def decode(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def parse_timed_text(text):
    return parse_timed_lines(text.split("\n"))[0]


def parse_timed_lines(lines):
    cues, timing_lines, current = [], [], None
    for index, line in enumerate(lines):
        timing = TIMING.match(line)
        if timing:
            if current:
                cues.append(finish_cue(current, followed_by_timing=True))
            current = (clock(timing.group(1)), clock(timing.group(2)), [])
            timing_lines.append(index)
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
    kept = [position for position, cue in enumerate(cues) if cue[2]]
    return [cues[position] for position in kept], [timing_lines[position] for position in kept]


def finish_cue(current, followed_by_timing):
    start, end, lines = current
    if followed_by_timing and lines and lines[-1].strip().isdigit():
        lines = lines[:-1]
    return start, end, "\n".join(lines).strip()


def parse_ass(lines):
    cues, events, columns, in_events = [], [], None, False
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
    return {"format": "ass", "cues": cues, "events": events, "columns": columns}


def clock(value):
    *larger, whole = value.replace(",", ".").split(":")
    total = float(whole)
    for unit, part in zip((60, 3600), reversed(larger), strict=False):
        total += unit * int(part)
    return total


def write_subtitle(path, document, timings):
    lines = ass_lines(document, timings) if document["format"] == "ass" else timed_lines(document, timings)
    replace_file(path, document["newline"].join(lines).encode(document["encoding"]))


def create_subtitle(path, cues, subtitle_format="srt"):
    separator = "." if subtitle_format == "vtt" else ","
    blocks = [f"{number}\n{timestamp(start, separator)} --> {timestamp(end, separator)}\n{text}\n" for number, (start, end, text) in enumerate(sorted(cues), 1)]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(("WEBVTT\n\n" if subtitle_format == "vtt" else "") + "\n".join(blocks))


def timed_lines(document, timings):
    lines = list(document["lines"])
    for line_index, (start, end, _) in zip(document["timing_lines"], timings, strict=False):
        line = lines[line_index]
        timing = TIMING.match(line)
        separator = "." if "." in timing.group(1) else ","
        lines[line_index] = (line[:timing.start(1)] + timestamp(start, separator) + line[timing.end(1):timing.start(2)] + timestamp(end, separator)
                             + line[timing.end(2):])
    return lines


def ass_lines(document, timings):
    lines, columns = list(document["lines"]), document["columns"]
    for line_index, timing in zip(document["events"], timings, strict=False):
        prefix, body = lines[line_index].split(":", 1)
        fields = body.split(",", len(columns) - 1)
        fields[columns.index("start")] = ass_timestamp(timing[0])
        fields[columns.index("end")] = ass_timestamp(timing[1])
        lines[line_index] = prefix + ":" + ",".join(fields)
    return lines


def timestamp(value, separator=","):
    milliseconds = max(0, round(value * 1000))
    return f"{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d}{separator}{milliseconds % 1000:03d}"


def ass_timestamp(value):
    centiseconds = max(0, round(value * 100))
    return f"{centiseconds // 360000}:{centiseconds // 6000 % 60:02d}:{centiseconds // 100 % 60:02d}.{centiseconds % 100:02d}"
