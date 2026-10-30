# SPDX-License-Identifier: GPL-3.0-or-later
"""Startup report: the settings a long-running command uses and the health of the tools, folders and Bazarr it depends on."""

import os
import shutil
import sys

from . import __version__, configuration, storage
from .audio import ARGUMENTS_CHECK_SCRIPT, FFSUBSYNC_LIBRARIES, ffsubsync_arguments
from .bazarr import BAZARR_CONFIG, bazarr_get
from .errors import ToolError
from .logs import LEVEL, log, settings
from .tools import run_tool, stderr_tail

IMPORT_CHECK_SECONDS = 60


def report_startup(command, **details):
    settings["source"] = command
    log("service.started", f"oncue {__version__} started command={command}", version=__version__, python=sys.version.split()[0], command=command,
        state=storage.STATE_DIRECTORY, backups=storage.BACKUP_DIRECTORY, bazarr_url=configuration.value("bazarr_url"), **details)
    checks = health_checks()
    failing = sorted(name for name, check in checks.items() if not check["ok"])
    log("health.checked", f"health degraded failing={','.join(failing)}" if failing else "health ok", LEVEL["warning"] if failing else LEVEL["info"],
        checks=checks)
    return checks


def health_checks():
    return {"ffmpeg": tool("ffmpeg"), "ffprobe": tool("ffprobe"), "ffsubsync": ffsubsync_status(),
            "state_writable": writable(storage.STATE_DIRECTORY), "backups_writable": writable(storage.BACKUP_DIRECTORY), "bazarr": bazarr_status()}


def ffsubsync_status():
    sample = ffsubsync_arguments("video.mkv", "in.srt", "out.srt", fix_framerate=False)
    try:
        result = run_tool([sys.executable, "-c", ARGUMENTS_CHECK_SCRIPT, FFSUBSYNC_LIBRARIES, *sample], IMPORT_CHECK_SECONDS)
    except ToolError as error:
        return {"ok": False, "path": FFSUBSYNC_LIBRARIES, "error": str(error)}
    return {"ok": result.returncode == 0, "path": FFSUBSYNC_LIBRARIES} | ({} if result.returncode == 0 else {"error": stderr_tail(result)})


def tool(name):
    path = shutil.which(name)
    return {"ok": bool(path), "path": path}


def writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as error:
        return {"ok": False, "path": folder, "error": str(error)}
    return {"ok": os.access(folder, os.W_OK), "path": folder}


def bazarr_status():
    if not configuration.value("bazarr_api_key") and not os.path.exists(BAZARR_CONFIG):
        return {"ok": True, "configured": False}
    try:
        status = bazarr_get("/api/system/status")["data"]
    except (OSError, KeyError, ValueError) as error:
        return {"ok": False, "configured": True, "url": configuration.value("bazarr_url"), "error": f"{type(error).__name__}: {error}"}
    return {"ok": True, "configured": True, "url": configuration.value("bazarr_url"), "version": status.get("bazarr_version")}
