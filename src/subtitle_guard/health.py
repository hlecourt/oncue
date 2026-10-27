"""Startup report: the settings a long-running command uses and the health of the tools, folders and Bazarr it depends on."""

import os
import shutil
import sys

from . import __version__, storage
from .audio import FFSUBSYNC_LIBRARIES
from .bazarr import BAZARR_CONFIG, BAZARR_URL, bazarr_get
from .logs import LEVEL, log, settings


def report_startup(command, **details):
    settings["source"] = command
    log("service.started", f"subtitle-guard {__version__} started command={command}", version=__version__, python=sys.version.split()[0], command=command,
        state=storage.STATE_DIRECTORY, backups=storage.BACKUP_DIRECTORY, bazarr_url=BAZARR_URL, **details)
    checks = health_checks()
    failing = sorted(name for name, check in checks.items() if not check["ok"])
    log("health.checked", f"health degraded failing={','.join(failing)}" if failing else "health ok", LEVEL["warning"] if failing else LEVEL["info"],
        checks=checks)
    return checks


def health_checks():
    return {"ffmpeg": tool("ffmpeg"), "ffprobe": tool("ffprobe"), "ffsubsync": {"ok": os.path.isdir(FFSUBSYNC_LIBRARIES), "path": FFSUBSYNC_LIBRARIES},
            "state_writable": writable(storage.STATE_DIRECTORY), "backups_writable": writable(storage.BACKUP_DIRECTORY), "bazarr": bazarr_status()}


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
    if not os.environ.get("BAZARR_API_KEY") and not os.path.exists(BAZARR_CONFIG):
        return {"ok": True, "configured": False}
    try:
        status = bazarr_get("/api/system/status")["data"]
    except (OSError, KeyError, ValueError) as error:
        return {"ok": False, "configured": True, "url": BAZARR_URL, "error": f"{type(error).__name__}: {error}"}
    return {"ok": True, "configured": True, "url": BAZARR_URL, "version": status.get("bazarr_version")}
