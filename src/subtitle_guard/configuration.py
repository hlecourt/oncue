# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings a person can change from the UI. An environment variable always wins over what the UI saved, and the allowed folders and the
confidence threshold are deliberately not here: they are set by the environment only."""

import http.client
import ipaddress
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .errors import ConfigError
from .storage import state_path

SETTINGS_FILE = "settings.json"
SOURCE = {"environment": "environment", "settings": "settings", "default": "default"}
MODE = {"report": "report", "fix": "fix"}
AUTH_MODE = {"forms": "forms", "local": "local", "external": "external"}
CACHE = {}
URL_BASE = re.compile(r"(/[A-Za-z0-9._-]+)+")
CLOUD_METADATA_ADDRESSES = (ipaddress.ip_address("fd00:ec2::254"), ipaddress.ip_address("100.100.100.200"))


@dataclass(frozen=True)
class Setting:
    variable: str
    default: object
    kind: str
    low: int = 0
    high: int = 0
    choices: tuple = ()
    secret: bool = False
    restart: bool = False


SETTINGS = {
    "bazarr_url": Setting("BAZARR_URL", "http://localhost:6767", "url"),
    "bazarr_api_key": Setting("BAZARR_API_KEY", "", "text", secret=True),
    "watched_folders": Setting("SUBTITLE_GUARD_LIBRARY", [], "folders"),
    "mode": Setting("SUBTITLE_GUARD_MODE", MODE["report"], "choice", choices=tuple(MODE)),
    "workers": Setting("SUBTITLE_GUARD_WORKERS", 2, "number", 1, 16),
    "watch_seconds": Setting("SUBTITLE_GUARD_WATCH_SECONDS", 600, "number", 60, 86400),
    "replacements": Setting("SUBTITLE_GUARD_REPLACEMENTS", 3, "number", 0, 10),
    "audio_automatic": Setting("SUBTITLE_GUARD_AUDIO_AUTOMATIC", False, "switch"),
    "keep_versions": Setting("SUBTITLE_GUARD_KEEP_VERSIONS", 5, "number", 1, 100),
    "keep_days": Setting("SUBTITLE_GUARD_KEEP_DAYS", 30, "number", 0, 3650),
    "webhook_url": Setting("SUBTITLE_GUARD_WEBHOOK_URL", "", "url"),
    "webhook_minutes": Setting("SUBTITLE_GUARD_WEBHOOK_MINUTES", 15, "number", 1, 1440),
    "url_base": Setting("SUBTITLE_GUARD_URL_BASE", "", "path", restart=True),
    "auth_mode": Setting("SUBTITLE_GUARD_AUTH", AUTH_MODE["forms"], "choice", choices=tuple(AUTH_MODE), restart=True),
}


def value(name):
    setting = SETTINGS[name]
    if os.environ.get(setting.variable) not in (None, ""):
        return parsed(setting, os.environ[setting.variable])
    stored = saved()
    return stored.get(name, setting.default)


def source(name):
    if os.environ.get(SETTINGS[name].variable) not in (None, ""):
        return SOURCE["environment"]
    return SOURCE["settings"] if name in saved() else SOURCE["default"]


def update(changes):
    unknown = [name for name in changes if name not in SETTINGS]
    if unknown:
        raise ConfigError(f"unknown settings: {', '.join(sorted(unknown))}")
    locked = [name for name in changes if source(name) == SOURCE["environment"]]
    if locked:
        raise ConfigError(f"set by the environment, change them there: {', '.join(sorted(locked))}")
    stored = {**saved(), **{name: validated(SETTINGS[name], new) for name, new in changes.items()}}
    path = state_path(SETTINGS_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".new"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(stored, handle, indent=1, sort_keys=True)
    os.replace(temporary, path)
    CACHE.clear()
    return stored


def saved():
    path = state_path(SETTINGS_FILE)
    try:
        modified = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return {}
    if CACHE.get("modified") != modified:
        with open(path) as handle:
            loaded = json.load(handle)
        CACHE.update(modified=modified, values={name: loaded[name] for name in SETTINGS if name in loaded})
    return CACHE["values"]


def parsed(setting, raw):
    if setting.kind == "folders":
        return [folder for folder in raw.split(os.pathsep) if folder]
    if setting.kind == "switch":
        return raw == "1"
    return validated(setting, int(raw) if setting.kind == "number" else raw)


def validated(setting, new):
    if setting.kind == "number":
        if isinstance(new, bool) or not isinstance(new, int) or not setting.low <= new <= setting.high:
            raise ConfigError(f"{setting.variable} must be a whole number from {setting.low} to {setting.high}")
        return new
    if setting.kind == "switch":
        if not isinstance(new, bool):
            raise ConfigError(f"{setting.variable} must be true or false")
        return new
    if setting.kind == "folders":
        if not isinstance(new, list) or not all(isinstance(folder, str) and os.path.isabs(folder) for folder in new):
            raise ConfigError(f"{setting.variable} must be a list of absolute folders")
        return new
    if not isinstance(new, str) or len(new) > 2000:
        raise ConfigError(f"{setting.variable} must be text")
    if setting.kind == "choice" and new not in setting.choices:
        raise ConfigError(f"{setting.variable} must be one of {', '.join(setting.choices)}")
    if setting.kind == "url" and new and urllib.parse.urlparse(new).scheme not in ("http", "https"):
        raise ConfigError(f"{setting.variable} must start with http:// or https://")
    if setting.kind == "path" and new and (not URL_BASE.fullmatch(new) or ".." in new):
        raise ConfigError(f"{setting.variable} must look like /subtitle-guard")
    return new


def outbound_opener(redirect_handler):
    return urllib.request.build_opener(CheckedHTTPHandler, CheckedHTTPSHandler, redirect_handler)


def refuse_link_local_peer(connection):
    address = ipaddress.ip_address(connection.sock.getpeername()[0])
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_link_local or address in CLOUD_METADATA_ADDRESSES:
        connection.close()
        raise ConfigError(f"refusing to call {address}: link-local and cloud metadata addresses are never called")


class CheckedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        refuse_link_local_peer(self)


class CheckedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        refuse_link_local_peer(self)


class CheckedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(CheckedHTTPConnection, request)


class CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(CheckedHTTPSConnection, request)
