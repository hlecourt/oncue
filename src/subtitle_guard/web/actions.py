# SPDX-License-Identifier: GPL-3.0-or-later
"""POST actions from the UI: fix, shift, revert, keep, move, retry, each on a subtitle the guard knows."""

import math
import os
import urllib.parse

from .. import jobs, review
from ..errors import EditRefusedError
from ..files import inside_roots
from ..library import SUBTITLE_EXTENSIONS
from ..reports import has_report
from .context import notice
from .parts import editor_link, seconds, short_clock, subtitle_link

MAX_MANUAL_SHIFT_SECONDS = 3600
SELECTION_MARGIN_SECONDS = 0.05


def perform(form):
    action, path = form.get("action", ""), form.get("path", "")
    if action == "retry-job":
        number = form.get("job", "")
        item = jobs.job(int(number)) if number.isascii() and number.isdigit() else None
        allowed = item is not None and all(inside_roots(item["arguments"][key]) for key in ("video", "subtitle") if item["arguments"].get(key))
        message = "It will run again shortly." if allowed and jobs.requeue(item["id"]) else "Only a finished task can be run again."
        return "/history?" + urllib.parse.urlencode({"notice": notice(message)})
    if not known_subtitle(path):
        return "/review?" + urllib.parse.urlencode({"notice": notice("Unknown subtitle, nothing was changed.")})
    video = review.video_for(path)
    if video is None:
        return subtitle_link(path, message="The video for this subtitle was not found, nothing was changed.")
    if action in ("fix-part", "shift"):
        try:
            first, last, shift = seconds(form.get("first", "")), seconds(form.get("last", "")), float(form.get("offset", "").replace(",", "."))
        except ValueError:
            return back_to(form, path, "Nothing was changed: the times must look like 2:03 or 1:02:03.50, and the move like -1.5.")
        if not all(math.isfinite(value) for value in (first, last, shift)) or abs(shift) > MAX_MANUAL_SHIFT_SECONDS:
            return back_to(form, path, f"Nothing was changed: a move must be a number of seconds up to {MAX_MANUAL_SHIFT_SECONDS}.")
        try:
            review.shift_range(video, path, first - SELECTION_MARGIN_SECONDS, last + SELECTION_MARGIN_SECONDS, shift, form.get("hash") or None)
        except EditRefusedError as error:
            return back_to(form, path, f"Nothing was changed: {error}.")
        message = f"Done. The lines from {short_clock(first)} to {short_clock(last)} now appear {abs(shift):.1f} s {'earlier' if shift < 0 else 'later'}."
    elif action == "fix-all":
        try:
            applied = review.apply_proposal(video, path, form.get("hash") or None)
        except EditRefusedError as error:
            return back_to(form, path, f"Nothing was changed: {error}.")
        message = "Done. The fix was applied and the previous file was backed up." if applied else "There is no fix to apply, so nothing was changed."
    elif action == "revert":
        message = "Done. The original file is back." if review.revert(path) else "There is no original file to go back to."
    elif action == "accept":
        review.accept(path)
        message = "OK. This subtitle leaves the list. It comes back only if the file changes."
    elif action == "recheck":
        review.recheck(video, path)
        message = "It will be checked again in the background."
    elif action == "another":
        asked = review.ask_for_another(video, path)
        message = "Bazarr is looking for another subtitle. It will be checked when it arrives." if asked else "This subtitle did not come from Bazarr."
    elif action == "delete":
        review.delete_subtitle(path)
        return "/review?" + urllib.parse.urlencode({"notice": notice(f"{os.path.basename(path)} was deleted. A copy was kept in the backups.")})
    else:
        message = "Nothing was changed."
    if form.get("back") == "review":
        return "/review?" + urllib.parse.urlencode({"notice": notice(f"{os.path.basename(path)}: {message}")})
    return back_to(form, path, message)


def back_to(form, path, message):
    if form.get("back") == "edit":
        return editor_link(path, form.get("at"), message)
    return subtitle_link(path, form.get("minute", "0"), message)


def known_subtitle(path):
    if not path.lower().endswith(SUBTITLE_EXTENSIONS) or not os.path.isfile(path) or os.path.islink(path):
        return False
    if not inside_roots(path):
        return False
    return has_report(path) or bool(jobs.for_subtitle(path, limit=1))
