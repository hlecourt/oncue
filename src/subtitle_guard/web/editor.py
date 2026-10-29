# SPDX-License-Identifier: GPL-3.0-or-later
"""The editor: listen to the video with the subtitle on a waveform, try a shift by ear, then apply it through the shift action."""

import json
import math
import os
import threading

from .. import review
from ..errors import ToolError
from ..files import file_hash
from ..logs import LEVEL, log
from ..preview import audio_clip, audio_tracks, still_frame, video_duration
from ..subtitles import parse_subtitle
from .actions import known_subtitle
from .parts import EDITOR, escape, layout, message_box, subtitle_link

MAX_START_SECONDS = 48 * 3600
MAX_TRACK_DIGITS = 3
PREVIEW_SLOT = threading.Lock()


def editor_page(query):
    path = query.get("path", "")
    if not known_subtitle(path):
        return 404, layout("Unknown subtitle", "<p>This subtitle is not known to the guard.</p>"), "text/html; charset=utf-8"
    video = review.video_for(path)
    if video is None:
        return 200, layout(os.path.basename(path), '<div class="status neutral"><b>The video for this subtitle was not found.</b></div>'), "text/html; charset=utf-8"
    cues = parse_subtitle(path)["cues"]
    reference = review.readable_reference(video, path, len(cues))
    texts = (review.reference_texts(video, reference) or [(start, end, "") for start, end in zip(reference["starts"], reference["ends"], strict=False)]) if reference else []
    try:
        tracks, duration = audio_tracks(video), video_duration(video)
    except ToolError as error:
        log("web.editor-unreadable", f"video unreadable for the editor: {error}", LEVEL["warning"], video=video, subtitle=path)
        return 200, layout(os.path.basename(path), '<div class="status neutral"><b>The video cannot be read, so it cannot be played here.</b></div>'), \
            "text/html; charset=utf-8"
    data = {"path": path, "hash": file_hash(path), "duration": duration, "tracks": tracks, "at": number_or_zero(query.get("at", "")),
            "cues": [[round(start, 3), round(end, 3), review.clean(text)] for start, end, text in cues],
            "reference": [[round(start, 3), round(end, 3), review.clean(text)] for start, end, text in texts], "reference_kind": reference["kind"] if reference else ""}
    page_data = json.dumps(data).replace("<", "\\u003c")
    body = (message_box(query) + f'<p class="subtle"><a href="{subtitle_link(path)}">Back to the check of this subtitle</a></p>'
            + editor_body(path, data) + f'<script type="application/json" id="editor-data">{page_data}</script><script>{EDITOR}</script>')
    return 200, layout(f"Edit {os.path.basename(path)}", body, current=path, wide=True), "text/html; charset=utf-8"


def editor_body(path, data):
    tracks = "".join(f'<option value="{track["index"]}">{escape(track_label(track))}</option>' for track in data["tracks"])
    track_picker = (f'<label class="track">Audio <select id="track">{tracks}</select></label>' if len(data["tracks"]) > 1
                    else f'<select id="track" hidden>{tracks}</select>')
    reference_note = ("Grey blocks under the waveform are the subtitles built into the video." if data["reference"]
                      else "The video has no subtitles of its own, so listen and move the lines until they match the voices.")
    return (
        '<div class="editor">'
        '<section class="editor-lines" aria-label="Lines of the subtitle"><table class="lines"><thead><tr><th>#</th><th>Start</th><th>End</th><th>Length</th>'
        '<th>Text</th></tr></thead><tbody id="lines"></tbody></table></section>'
        '<section class="editor-side" aria-label="Preview">'
        '<div class="stage"><img id="frame" alt="Picture of the video at the current time"><p class="caption" id="caption"></p></div>'
        '<p class="subtle words" id="audio-status" role="status">Press Play to listen. The picture updates when you pause.</p></section>'
        '<section class="editor-controls" aria-label="Playback and moving the lines"><div class="transport">'
        '<button type="button" id="back">Back 5 s</button><button type="button" class="primary" id="play">Play</button>'
        f'<button type="button" id="forward">Forward 5 s</button><span class="clock" id="clock">0:00.0 / {clock_text(data["duration"])}</span>'
        '<label class="speed">Speed <select id="speed"><option value="0.75">0.75x</option><option value="1" selected>1x</option><option value="1.25">1.25x</option></select></label>'
        f'{track_picker}</div>'
        '<div class="shift-panel"><h2>Move the lines</h2>'
        '<p class="shift-words" id="shift-words" aria-live="polite">Not moved.</p>'
        '<div class="nudges"><button type="button" data-nudge="-1">Earlier 1 s</button><button type="button" data-nudge="-0.1">Earlier 0.1 s</button>'
        '<button type="button" data-nudge="0.1">Later 0.1 s</button><button type="button" data-nudge="1">Later 1 s</button>'
        '<label for="shift">Move by (s)</label><input type="number" id="shift" step="0.05" value="0"><button type="button" id="reset">Reset</button></div>'
        '<fieldset class="scope"><legend>Lines to move</legend>'
        '<label><input type="radio" name="scope" value="all" checked> Every line</label>'
        '<label><input type="radio" name="scope" value="selected" id="scope-selected" disabled> <span id="selection-words">Selected lines (click a line, shift-click another)</span></label>'
        '</fieldset>'
        f'<form method="post" id="apply-form"><input type="hidden" name="path" value="{escape(path)}"><input type="hidden" name="action" value="shift">'
        f'<input type="hidden" name="hash" value="{escape(data["hash"])}"><input type="hidden" name="back" value="edit"><input type="hidden" name="at" id="at" value="0">'
        '<input type="hidden" name="first" id="first"><input type="hidden" name="last" id="last"><input type="hidden" name="offset" id="offset" value="0">'
        '<button class="primary" id="apply" disabled>Apply to the file</button></form>'
        '<p class="subtle words">Nothing is written until you apply. The file is backed up first and can be restored from its check page.</p></div>'
        '</section>'
        '<section class="editor-timeline" aria-label="Waveform">'
        '<canvas id="wave" height="150">Waveform of the audio with the lines of the subtitle</canvas>'
        '<div class="timeline-bar"><button type="button" id="zoom-out">Zoom out</button><span id="zoom-words">50 px/s</span>'
        '<button type="button" id="zoom-in">Zoom in</button>'
        '<span class="legend-item"><span class="swatch yours"></span>Your subtitle</span>'
        + ('<span class="legend-item"><span class="swatch video"></span>In the video</span>' if data["reference"] else "")
        + f'<span class="subtle">{escape(reference_note)} Click to go there, drag your lines sideways to move them.</span></div>'
        '</section></div>')


def api_audio(form):
    video, problem = media_video(form)
    if problem:
        return problem
    start = number_or_zero(form.get("start", ""))
    track = form.get("track", "")
    if not (track.isascii() and track.isdigit() and len(track) <= MAX_TRACK_DIGITS) or not 0 <= start <= MAX_START_SECONDS:
        return 400, "bad start or track", "text/plain; charset=utf-8"
    return run_preview(lambda: audio_clip(video, start, int(track)), "audio/wav", video)


def api_frame(form):
    video, problem = media_video(form)
    if problem:
        return problem
    moment = number_or_zero(form.get("time", ""))
    if not 0 <= moment <= MAX_START_SECONDS:
        return 400, "bad time", "text/plain; charset=utf-8"
    return run_preview(lambda: still_frame(video, moment), "image/jpeg", video)


def media_video(form):
    path = form.get("path", "")
    if not known_subtitle(path):
        return None, (404, "unknown subtitle", "text/plain; charset=utf-8")
    video = review.video_for(path)
    if video is None:
        return None, (404, "video not found", "text/plain; charset=utf-8")
    return video, None


def run_preview(extract, content_type, video):
    if not PREVIEW_SLOT.acquire(blocking=False):
        return 503, "busy, try again", "text/plain; charset=utf-8"
    try:
        return 200, extract(), content_type
    except ToolError as error:
        log("web.preview-failed", f"preview extraction failed: {error}", LEVEL["warning"], video=video)
        return 422, "the video could not be read here", "text/plain; charset=utf-8"
    finally:
        PREVIEW_SLOT.release()


def number_or_zero(value):
    try:
        number = float(value)
    except ValueError:
        return 0.0
    return number if math.isfinite(number) else 0.0


def track_label(track):
    words = [track["language"] or "unknown language", track["title"], f"{track['channels']} ch" if track["channels"] else "", "commentary" if track["commentary"] else ""]
    return ", ".join(word for word in words if word)


def clock_text(value):
    whole = int(value)
    return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}" if whole >= 3600 else f"{whole // 60}:{whole % 60:02d}"

