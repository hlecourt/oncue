# SPDX-License-Identifier: GPL-3.0-or-later
"""Read subtitle timings and texts from the Matroska Cues index without reading the whole file."""

from functools import wraps

from .errors import MatroskaError
from .files import opened_video

MATROSKA_HEAD_BYTES = 64 * 1024
MAX_ELEMENT_BYTES = 64 * 1024 * 1024
MATROSKA_ID = {"segment": 0x18538067, "seek_head": 0x114D9B74, "seek": 0x4DBB, "seek_id": 0x53AB, "cue_point": 0xBB, "seek_position": 0x53AC, "info": 0x1549A966,
               "timestamp_scale": 0x2AD7B1, "tracks": 0x1654AE6B, "track_entry": 0xAE, "track_number": 0xD7, "cues": 0x1C53BB6B,
               "cue_time": 0xB3, "cue_track_positions": 0xB7, "cue_track": 0xF7, "cue_cluster_position": 0xF1,
               "cue_relative_position": 0xF0, "cue_duration": 0xB2, "block_group": 0xA0, "block": 0xA1, "simple_block": 0xA3}


def typed_errors(function):
    @wraps(function)
    def wrapper(*arguments, **options):
        try:
            return function(*arguments, **options)
        except (IndexError, KeyError, StopIteration, ValueError) as error:
            raise MatroskaError(f"unreadable Matroska index: {type(error).__name__}: {error}") from error
    return wrapper


@typed_errors
def matroska_cue_timings(video, stream_count, split_bitmap_streams):
    with opened_video(video) as descriptor, open(descriptor, "rb", closefd=False) as handle:
        index = read_index(handle, stream_count)
        if index is None:
            return {}
        segment_start, entries = index
        timings = {stream: [(start, duration, None) for start, duration, _ in items] for stream, items in entries.items() if stream not in split_bitmap_streams}
        for stream in split_bitmap_streams:
            items = entries.get(stream, [])
            if items and all(location for _, _, location in items):
                timings[stream] = block_sizes(handle, segment_start, items)
    return timings


@typed_errors
def matroska_texts(video, stream_count, stream, ass=False):
    with opened_video(video) as descriptor, open(descriptor, "rb", closefd=False) as handle:
        index = read_index(handle, stream_count)
        if index is None:
            return None
        segment_start, entries = index
        items = entries.get(stream, [])
        if not items or not all(location for _, _, location in items):
            return None
        cluster_header_lengths, texts = {}, []
        for start, duration, (cluster, relative) in sorted(items, key=lambda item: item[2]):
            payload = block_payload(handle, block_position(handle, segment_start, cluster, relative, cluster_header_lengths))
            if payload is None:
                return None
            line = payload.decode("utf-8", errors="replace")
            texts.append((start, start + (duration or 2.0), line.split(",", 8)[-1] if ass else line))
    return sorted(texts)


def read_index(handle, stream_count):
    head = handle.read(MATROSKA_HEAD_BYTES)
    segment_start = next((start for element_id, start, _ in ebml_children(head, 0, len(head)) if element_id == MATROSKA_ID["segment"]), None)
    if segment_start is None:
        return None
    positions = seek_positions(head, segment_start)
    if MATROSKA_ID["cues"] not in positions and MATROSKA_ID["seek_head"] in positions:
        more = read_element(handle, positions[MATROSKA_ID["seek_head"]])
        positions = {**seeks_in(more, 0, len(more), segment_start), **positions}
    if not all(MATROSKA_ID[name] in positions for name in ("info", "tracks", "cues")):
        return None
    info = read_element(handle, positions[MATROSKA_ID["info"]])
    scale_field = next((field for field in ebml_children(info, 0, len(info)) if field[0] == MATROSKA_ID["timestamp_scale"]), None)
    seconds_per_tick = (unsigned(info, *scale_field[1:]) if scale_field else 1_000_000) / 1e9
    tracks = read_element(handle, positions[MATROSKA_ID["tracks"]])
    track_numbers = [unsigned(tracks, *field[1:]) for element_id, start, end in ebml_children(tracks, 0, len(tracks))
                     if element_id == MATROSKA_ID["track_entry"] for field in ebml_children(tracks, start, end) if field[0] == MATROSKA_ID["track_number"]]
    if len(track_numbers) != stream_count:
        return None
    stream_by_track = {number: index for index, number in enumerate(track_numbers)}
    cues = read_element(handle, positions[MATROSKA_ID["cues"]])
    entries = {}
    for point_id, point_start, point_end in ebml_children(cues, 0, len(cues)):
        if point_id != MATROSKA_ID["cue_point"]:
            continue
        fields = list(ebml_children(cues, point_start, point_end))
        ticks = next(unsigned(cues, start, end) for element_id, start, end in fields if element_id == MATROSKA_ID["cue_time"])
        for element_id, start, end in fields:
            if element_id != MATROSKA_ID["cue_track_positions"]:
                continue
            inner = {child: (child_start, child_end) for child, child_start, child_end in ebml_children(cues, start, end)}
            stream = stream_by_track.get(unsigned(cues, *inner[MATROSKA_ID["cue_track"]]))
            duration = unsigned(cues, *inner[MATROSKA_ID["cue_duration"]]) * seconds_per_tick if MATROSKA_ID["cue_duration"] in inner else None
            location = (unsigned(cues, *inner[MATROSKA_ID["cue_cluster_position"]]),
                        unsigned(cues, *inner[MATROSKA_ID["cue_relative_position"]])) if MATROSKA_ID["cue_relative_position"] in inner else None
            entries.setdefault(stream, []).append((ticks * seconds_per_tick, duration, location))
    return segment_start, entries


def block_sizes(handle, segment_start, items):
    cluster_header_lengths, sized = {}, []
    for start, duration, (cluster, relative) in sorted(items, key=lambda item: item[2]):
        handle.seek(block_position(handle, segment_start, cluster, relative, cluster_header_lengths))
        block = handle.read(32)
        element_id, position = read_vint(block, 0, keep_marker=True)
        size, position = read_vint(block, position)
        if element_id == MATROSKA_ID["block_group"]:
            size = next((end - start for child, start, end in ebml_children(block, position, len(block), clip=False) if child == MATROSKA_ID["block"]), None)
        sized.append((start, duration, size))
    return sized


def block_position(handle, segment_start, cluster, relative, cluster_header_lengths):
    cluster_position = segment_start + cluster
    if cluster_position not in cluster_header_lengths:
        handle.seek(cluster_position)
        header = handle.read(16)
        _, position = read_vint(header, 0, keep_marker=True)
        _, position = read_vint(header, position)
        cluster_header_lengths[cluster_position] = position
    return cluster_position + cluster_header_lengths[cluster_position] + relative


def block_payload(handle, position):
    content = read_element_with_id(handle, position)
    if content is None:
        return None
    element_id, data = content
    if element_id == MATROSKA_ID["block_group"]:
        data = next((data[start:end] for child, start, end in ebml_children(data, 0, len(data)) if child == MATROSKA_ID["block"]), None)
        if data is None:
            return None
    elif element_id != MATROSKA_ID["simple_block"]:
        return None
    _, offset = read_vint(data, 0)
    return data[offset + 3:]


def read_element_with_id(handle, position):
    handle.seek(position)
    header = handle.read(16)
    element_id, data_start = read_vint(header, 0, keep_marker=True)
    size, data_start = read_vint(header, data_start)
    if size > MAX_ELEMENT_BYTES:
        raise MatroskaError(f"element of {size} bytes is larger than {MAX_ELEMENT_BYTES}")
    handle.seek(position + data_start)
    return element_id, handle.read(size)


def seek_positions(head, segment_start):
    for element_id, start, end in ebml_children(head, segment_start, len(head)):
        if element_id == MATROSKA_ID["seek_head"]:
            return seeks_in(head, start, end, segment_start)
    return {}


def seeks_in(data, start, end, segment_start):
    positions = {}
    for element_id, seek_start, seek_end in ebml_children(data, start, end):
        if element_id != MATROSKA_ID["seek"]:
            continue
        fields = {field: (field_start, field_end) for field, field_start, field_end in ebml_children(data, seek_start, seek_end)}
        positions[unsigned(data, *fields[MATROSKA_ID["seek_id"]])] = segment_start + unsigned(data, *fields[MATROSKA_ID["seek_position"]])
    return positions


def read_element(handle, position):
    return read_element_with_id(handle, position)[1]


def ebml_children(data, start, end, clip=True):
    position = start
    while position < end:
        element_id, position = read_vint(data, position, keep_marker=True)
        size, position = read_vint(data, position)
        yield element_id, position, min(position + size, end) if clip else position + size
        position += size


def read_vint(data, position, keep_marker=False):
    first = data[position]
    if first == 0:
        raise MatroskaError("invalid EBML length")
    length = 9 - first.bit_length()
    value = first if keep_marker else first & (0xFF >> length)
    for byte in data[position + 1:position + length]:
        value = value << 8 | byte
    return value, position + length


def unsigned(data, start, end):
    return int.from_bytes(data[start:end], "big")
