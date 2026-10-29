# SPDX-License-Identifier: GPL-3.0-or-later
"""The settings page: every setting with its value and where it comes from, and the two values that only the environment sets."""

from fastapi import APIRouter, Depends, HTTPException

from ... import configuration
from ...calibration import calibration_allows_automatic
from ...errors import ConfigError
from ...files import ROOTS, inside_roots
from ...guard import AUTOMATIC_BAND
from ..schemas import ApiKey, FixedValue, SettingsChange, SettingsView, SettingView
from ..security import api_key, regenerate_api_key, require_csrf, require_user

router = APIRouter(prefix="/settings", tags=["settings"])
HIGH_CONFIDENCE = 80


@router.get("", operation_id="getSettings", dependencies=[Depends(require_user)])
def get_settings() -> SettingsView:
    return settings_view()


@router.patch("", operation_id="changeSettings", dependencies=[Depends(require_csrf)])
def change_settings(body: SettingsChange) -> SettingsView:
    outside = [folder for folder in body.changes.get("watched_folders") or [] if isinstance(folder, str) and not inside_roots(folder)]
    if outside:
        raise HTTPException(422, {"code": "outside_allowed_folders", "message": f"Not inside the allowed folders: {', '.join(outside)}."})
    if "bazarr_url" in body.changes and body.changes["bazarr_url"] != configuration.value("bazarr_url") and not body.changes.get("bazarr_api_key"):
        raise HTTPException(422, {"code": "bazarr_key_needed", "message": "Enter Bazarr's API key again with the new address: the key is only sent to "
                                                                          "the address it was entered for."})
    if body.changes.get("audio_automatic") is True:
        allowed, why = calibration_allows_automatic()
        if not allowed:
            raise HTTPException(409, {"code": "not_calibrated", "message": why})
    try:
        configuration.update(body.changes)
    except ConfigError as error:
        raise HTTPException(422, {"code": "invalid_setting", "message": str(error)}) from error
    return settings_view()


@router.get("/api-key", operation_id="getApiKey", dependencies=[Depends(require_user)])
def get_api_key() -> ApiKey:
    return ApiKey(key=api_key())


@router.post("/api-key", operation_id="regenerateApiKey", dependencies=[Depends(require_csrf)])
def new_api_key() -> ApiKey:
    return ApiKey(key=regenerate_api_key())


def settings_view():
    views = []
    for name, setting in configuration.SETTINGS.items():
        current = configuration.value(name)
        views.append(SettingView(name=name, value=None if setting.secret else current, is_set=bool(current), source=configuration.source(name),
                                 locked=configuration.source(name) == configuration.SOURCE["environment"], restart=setting.restart, kind=setting.kind,
                                 low=setting.low, high=setting.high, choices=list(setting.choices),
                                 note=calibration_allows_automatic()[1] if name == "audio_automatic" else None))
    fixed = [FixedValue(name="allowed_folders", value=ROOTS, why="Set by SUBTITLE_GUARD_ROOTS only: it limits where the guard may ever write."),
             FixedValue(name="automatic_fix_confidence", value=f"{HIGH_CONFIDENCE} ({AUTOMATIC_BAND})", why="Fixed: a fix is written on its own only at high confidence.")]
    return SettingsView(settings=views, fixed=fixed)
