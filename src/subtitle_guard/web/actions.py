"""POST actions from the UI: fix, shift, revert, keep, move, retry, each on a subtitle the guard knows."""

import os
import urllib.parse

from .. import jobs, review
from ..errors import SubtitleGuardError
from ..library import SUBTITLE_EXTENSIONS
from ..reports import has_report
from .context import notice
from .parts import seconds, short_clock, subtitle_link

ROOTS = [os.path.realpath(root) for root in os.environ.get("SUBTITLE_GUARD_ROOTS", os.environ.get("SUBTITLE_GUARD_LIBRARY", "")).split(os.pathsep) if root]


def perform(form):
    action, path = form.get("action", ""), form.get("path", "")
    if action == "retry-job":
        job = form.get("job", "")
        message = "It will run again shortly." if job.isdigit() and jobs.requeue(int(job)) else "Only a finished task can be run again."
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
            return subtitle_link(path, form.get("minute", "0"), "Nothing was changed: the times must look like 2:03 or 1:02:03.50, and the move like -1.5.")
        review.shift_range(video, path, first - 0.05, last + 0.05, shift)
        message = f"Done. The lines from {short_clock(first)} to {short_clock(last)} now appear {abs(shift):.1f} s {'earlier' if shift < 0 else 'later'}."
    elif action == "fix-all":
        fixed = review.fix_everything(video, path)["decision"] == "fixed"
        message = "Done. The whole subtitle is back in sync." if fixed else "It could not be fixed safely, so nothing was changed."
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
    elif action == "move":
        try:
            moved = review.move_to_episode(video, path, form.get("episode", ""))
        except SubtitleGuardError as error:
            return subtitle_link(path, message=f"Nothing was changed: {error}")
        if moved is None:
            message = "That episode was not found next to this one, nothing was changed."
        else:
            path, message = moved[0], f"Moved to {form['episode']}. It will be checked there."
    else:
        message = "Nothing was changed."
    if form.get("back") == "review":
        return "/review?" + urllib.parse.urlencode({"notice": notice(f"{os.path.basename(path)}: {message}")})
    return subtitle_link(path, None if action == "move" else form.get("minute", "0"), message)


def known_subtitle(path):
    if not path.lower().endswith(SUBTITLE_EXTENSIONS) or not os.path.isfile(path) or os.path.islink(path):
        return False
    if ROOTS and not any(os.path.realpath(path).startswith(root + os.sep) for root in ROOTS):
        return False
    return has_report(path) or bool(jobs.for_subtitle(path, limit=1))
