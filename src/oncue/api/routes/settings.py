# SPDX-License-Identifier: GPL-3.0-or-later
"""The settings page: every setting with its value and where it comes from, and the two values that only the environment sets."""

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import configuration
from ...calibration import calibration_allows_automatic
from ...errors import ConfigError
from ...files import ALLOWED_FOLDERS, inside_allowed_folders
from ...guard import AUTOMATIC_BAND
from ..auth import api_key, current_account, regenerate_api_key
from ..i18n import tr
from ..schemas import ApiKey, FixedValue, PasswordConfirmation, Problem, SettingsChange, SettingsView, SettingView
from ..security import login_needed, password_accepted, require_csrf, require_user

router = APIRouter(prefix="/settings", tags=["settings"])
HIGH_CONFIDENCE = 80
PASSWORD_SETTINGS = ("auth_mode", "url_base")


@router.get("", operation_id="getSettings", dependencies=[Depends(require_user)])
def get_settings() -> SettingsView:
    return settings_view()


@router.patch("", operation_id="changeSettings", dependencies=[Depends(require_csrf)], responses={403: {"model": Problem}, 429: {"model": Problem}})
def change_settings(body: SettingsChange, request: Request) -> SettingsView:
    if any(name in body.changes for name in PASSWORD_SETTINGS):
        require_password(request, body.password)
    outside = [folder for folder in body.changes.get("watched_folders") or [] if isinstance(folder, str) and not inside_allowed_folders(folder)]
    if outside:
        raise HTTPException(422, {"code": "outside_allowed_folders", "message": tr("Not inside the allowed folders: {folders}.", folders=", ".join(outside))})
    if "bazarr_url" in body.changes and body.changes["bazarr_url"] != configuration.value("bazarr_url") and not body.changes.get("bazarr_api_key"):
        raise HTTPException(422, {"code": "bazarr_key_needed", "message": tr("Enter Bazarr's API key again with the new address: the key is only sent to "
                                                                             "the address it was entered for.")})
    url_from_settings = "bazarr_url" in body.changes or configuration.source("bazarr_url") == configuration.SOURCE["settings"]
    if "bazarr_api_key" in body.changes and not body.changes["bazarr_api_key"] and url_from_settings:
        raise HTTPException(422, {"code": "bazarr_key_needed", "message": tr("Bazarr's API key cannot be emptied while its address is set in Settings: "
                                                                             "enter the key for that address.")})
    if body.changes.get("audio_automatic") is True:
        allowed, why, values = calibration_allows_automatic()
        if not allowed:
            raise HTTPException(409, {"code": "not_calibrated", "message": tr(why, **values)})
    try:
        configuration.update(body.changes)
    except ConfigError as error:
        raise HTTPException(422, {"code": "invalid_setting", "message": str(error)}) from error
    return settings_view()


@router.post("/api-key/reveal", operation_id="revealApiKey", dependencies=[Depends(require_csrf)], responses={403: {"model": Problem}, 429: {"model": Problem}})
def reveal_api_key(body: PasswordConfirmation, request: Request) -> ApiKey:
    require_password(request, body.password)
    return ApiKey(key=api_key())


@router.post("/api-key", operation_id="regenerateApiKey", dependencies=[Depends(require_csrf)], responses={403: {"model": Problem}, 429: {"model": Problem}})
def new_api_key(body: PasswordConfirmation, request: Request) -> ApiKey:
    require_password(request, body.password)
    return ApiKey(key=regenerate_api_key())


def require_password(request, password):
    if login_needed(request) and not password_accepted(request, current_account()["user"], password):
        raise HTTPException(403, {"code": "wrong_password", "message": tr("That password is not right.")})


def settings_view():
    views = []
    for name, setting in configuration.SETTINGS.items():
        current = configuration.value(name)
        views.append(SettingView(name=name, value=None if setting.secret else current, is_set=bool(current), source=configuration.source(name),
                                 locked=configuration.source(name) == configuration.SOURCE["environment"], restart=setting.restart, kind=setting.kind,
                                 low=setting.low, high=setting.high, choices=list(setting.choices),
                                 note=calibration_note() if name == "audio_automatic" else None))
    fixed = [FixedValue(name="allowed_folders", value=ALLOWED_FOLDERS, why=tr("Set by ONCUE_ROOTS only: it limits where OnCue may ever write.")),
             FixedValue(name="automatic_fix_confidence", value=f"{HIGH_CONFIDENCE} ({AUTOMATIC_BAND})", why=tr("Fixed: a fix is written on its own only at high confidence."))]
    return SettingsView(settings=views, fixed=fixed)


def calibration_note():
    _, why, values = calibration_allows_automatic()
    return tr(why, **values)
