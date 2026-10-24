"""subtitle-guard: check subtitle timing against a track embedded in the video, and repair or replace it.

Commands:
  hook VIDEO SUBTITLE LANGUAGE PROVIDER SUBTITLE_ID MEDIA_ID [SERIES_ID]   Bazarr post-processing entry point
  check VIDEO SUBTITLE [--apply]                                            one file, dry run unless --apply
  scan-series SERIES_ID [--apply]                                           every external subtitle of a Bazarr series
  scan-movies [--apply]                                                     every external subtitle of every Bazarr movie
  scan-all [--apply]                                                        every Bazarr series, then every movie
  needs-human                                                               subtitles whose last check needs a person
  try-another VIDEO SUBTITLE                                                internal: blacklist and let Bazarr retry

Settings (environment): SUBTITLE_GUARD_STATE, SUBTITLE_GUARD_BACKUPS, SUBTITLE_GUARD_FFSUBSYNC_PATH, BAZARR_URL, BAZARR_API_KEY or BAZARR_CONFIG.
"""

import os
import subprocess
import sys

from .bazarr import bazarr_get, handle_download, scan_movies, scan_series, try_another_subtitle
from .guard import evaluate_and_fix
from .outcome import describe
from .storage import print_needs_human, report


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    command, arguments = sys.argv[1], sys.argv[2:]
    apply = "--apply" in arguments
    arguments = [argument for argument in arguments if argument != "--apply"]
    lower_io_priority()
    if command == "hook" and len(arguments) >= 6:
        return handle_download(*arguments)
    if command == "check" and len(arguments) == 2:
        outcome = evaluate_and_fix(arguments[0], arguments[1], apply)
        if apply:
            report(arguments[1], "check", outcome)
        print(describe(outcome))
        return 0
    if command == "scan-series" and len(arguments) == 1:
        return scan_series(int(arguments[0]), apply)
    if command == "scan-movies":
        return scan_movies(apply)
    if command == "scan-all":
        for series in bazarr_get("/api/series?start=0&length=-1")["data"]:
            scan_series(series["sonarrSeriesId"], apply)
        return scan_movies(apply)
    if command == "needs-human":
        return print_needs_human()
    if command == "try-another" and len(arguments) == 2:
        return try_another_subtitle(*arguments)
    print(__doc__)
    return 2


def lower_io_priority():
    try:
        subprocess.run(["ionice", "-c2", "-n7", "-p", str(os.getpid())], capture_output=True)
    except OSError:
        pass
