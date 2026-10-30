# SPDX-License-Identifier: GPL-3.0-or-later
"""The System page: tool and folder health, workers, backups and their clean-up, Bazarr's own settings, and library scans."""

from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException

from ... import __version__, configuration, files, jobs, storage
from ...bazarr import setup_warnings
from ...errors import ConfigError
from ...files import clean_up_backups, folder_bytes
from ...health import health_checks
from ...heartbeats import worker_health
from ...logs import LEVEL, log
from ...scans import bazarr_configured, latest_scan, scan, start_scan, stop_scan
from ..i18n import tr
from ..schemas import BazarrSetup, Check, CleanUp, Scan, SetupWarning, SystemView
from ..security import require_csrf, require_user

router = APIRouter(tags=["system"])


@router.get("/system", operation_id="getSystem", dependencies=[Depends(require_user)])
def get_system() -> SystemView:
    checks = {name: Check(ok=check["ok"], detail=check_detail(check)) for name, check in health_checks().items()}
    checks["allowed_folders"] = Check(ok=bool(files.ALLOWED_FOLDERS), detail=", ".join(files.ALLOWED_FOLDERS) or tr("Not set: no file is changed until SUBTITLE_GUARD_ROOTS is set"))
    return SystemView(version=__version__, checks=checks, workers=worker_health(), backups_bytes=folder_bytes(storage.BACKUP_DIRECTORY),
                      mode=configuration.value("mode"))


def check_detail(check):
    if check.get("error"):
        return str(check["error"]).strip().splitlines()[-1]
    return str(check.get("version") or check.get("path") or (tr("not set up") if check.get("configured") is False else ""))


@router.get("/system/bazarr", operation_id="checkBazarrSetup", dependencies=[Depends(require_user)])
def check_bazarr() -> BazarrSetup:
    if not bazarr_configured():
        return BazarrSetup(reachable=False, warnings=[SetupWarning(code="not_set_up", message=tr("Bazarr is not set up, so the guard works from the watched "
                                                                                             "folders. Add its address and API key in Settings to use it."))])
    try:
        return BazarrSetup(reachable=True, warnings=[SetupWarning(code=warning["code"], message=tr(warning["message"])) for warning in setup_warnings()])
    except (OSError, ValueError, KeyError, ConfigError) as error:
        log("bazarr.setup-check-failed", f"Bazarr setup check failed: {type(error).__name__}: {error}", LEVEL["warning"], error=str(error))
        return BazarrSetup(reachable=False, warnings=[SetupWarning(code="unreachable", message=tr("Bazarr cannot be reached with this address and API key. "
                                                                                                    "The log has the details."))])


@router.post("/system/backups/clean-up", operation_id="cleanUpBackups", dependencies=[Depends(require_csrf)])
def clean_up() -> CleanUp:
    with closing(jobs.connect()) as connection:
        known = [row[0] for row in connection.execute("select path from subtitles union select distinct subtitle from reports").fetchall()]
    return CleanUp(removed=clean_up_backups(known), backups_bytes=folder_bytes(storage.BACKUP_DIRECTORY))


@router.post("/scans", operation_id="startScan", status_code=202, dependencies=[Depends(require_csrf)])
def start() -> Scan:
    return Scan(**scan(start_scan()))


@router.get("/scans/latest", operation_id="latestScan", dependencies=[Depends(require_user)])
def latest() -> Scan | None:
    found = latest_scan()
    return Scan(**found) if found else None


@router.post("/scans/{scan_id}/stop", operation_id="stopScan", dependencies=[Depends(require_csrf)])
def stop(scan_id: int) -> Scan:
    if scan(scan_id) is None:
        raise HTTPException(404, {"code": "unknown_scan", "message": tr("There is no such scan.")})
    stop_scan(scan_id)
    return Scan(**scan(scan_id))
