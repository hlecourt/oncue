# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the library the browser tests run against: one film with sound, an embedded English track and a French subtitle 3 s late in a part,
checked in report mode so it waits in Review with a proposal, and one subtitle that belongs to another episode.

usage: python tests/e2e_library.py FOLDER (prints the environment the API needs)"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

folder = Path(sys.argv[1]).resolve()
os.environ["SUBTITLE_GUARD_STATE"] = str(folder / "state")
os.environ["SUBTITLE_GUARD_BACKUPS"] = str(folder / "backup")

from media import video_with_reference, write_subtitle_file  # noqa: E402

from subtitle_guard import reports  # noqa: E402
from subtitle_guard.guard import evaluate_and_fix  # noqa: E402

video, starts, ends = video_with_reference(folder, minutes=3, with_audio=True)
late = video.with_suffix(".fr.srt")
write_subtitle_file(late, starts, ends, lambda value: value + 3 if 60 <= value < 120 else value)
reports.report(str(late), "scan", evaluate_and_fix(str(video), str(late), False), applied=False)
other = video.with_suffix(".de.srt")
write_subtitle_file(other, starts, ends, lambda value: value)
reports.report(str(other), "hook", {"decision": "wrong-episode", "matches": "S01E03", "confidence": 0, "confidence_band": "none"})
print(f"SUBTITLE_GUARD_STATE={folder / 'state'} SUBTITLE_GUARD_BACKUPS={folder / 'backup'} SUBTITLE_GUARD_ROOTS={folder / 'library'}")
