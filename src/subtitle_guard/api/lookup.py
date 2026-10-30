# SPDX-License-Identifier: GPL-3.0-or-later
"""From an opaque subtitle id to a path the API may act on: known to the guard, a regular file inside the allowed folders, never a symlink."""

import os

from fastapi import HTTPException

from .. import jobs
from ..catalog import path_for
from ..files import inside_allowed_folders
from ..library import SUBTITLE_EXTENSIONS
from ..reports import has_report
from .i18n import tr


def known_path(identity):
    path = path_for(identity)
    if path is None or not usable(path):
        raise HTTPException(404, {"code": "unknown_subtitle", "message": tr("This subtitle is not known to the guard.")})
    return path


def usable(path):
    if not path.lower().endswith(SUBTITLE_EXTENSIONS) or not os.path.isfile(path) or os.path.islink(path) or not inside_allowed_folders(path):
        return False
    return has_report(path) or bool(jobs.for_subtitle(path, limit=1))
