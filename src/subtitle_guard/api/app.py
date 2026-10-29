# SPDX-License-Identifier: GPL-3.0-or-later
"""The API application and its server; the engine, CLI and Bazarr hook never import this module."""

import ipaddress
import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .. import __version__, configuration, logs
from ..errors import ConfigError
from ..logs import log
from .auth import configure
from .front import add_front
from .i18n import tr
from .routes import activity, commands, media, review, session, settings, status, subtitles, system
from .security import SECURITY_HEADERS, guard, new_csrf_token

HOST = os.environ.get("SUBTITLE_GUARD_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("SUBTITLE_GUARD_UI_PORT", "8484"))
MAX_CONNECTIONS = 32
KEEP_ALIVE_SECONDS = 5


def create_app(host=HOST):
    base = configuration.value("url_base")
    app = FastAPI(title="subtitle-guard", version=__version__, docs_url=None, redoc_url=None, openapi_url=f"{base}/api/openapi.json")
    app.state.auth_mode = configuration.value("auth_mode")
    app.state.login_required = configure(host)
    app.state.trusted_proxies = trusted_proxies()
    app.state.anonymous_csrf = new_csrf_token()
    app.state.open_paths = {f"{prefix}{path}" for prefix in {"", base} for path in ("/health", "/ready")}
    app.middleware("http")(guard)
    app.add_exception_handler(HTTPException, http_problem)
    app.add_exception_handler(RequestValidationError, invalid_request)
    for router in (session.router, review.router, subtitles.router, commands.router, activity.router, settings.router, system.router, media.router):
        app.include_router(router, prefix=f"{base}/api")
    app.include_router(status.router, prefix=base)
    for prefix in {"", base}:
        app.include_router(status.probes, prefix=prefix)
    add_front(app, base)
    return app


def serve_api(host=HOST, port=PORT):
    logs.settings["source"] = "web"
    app = create_app(host)
    log("web.listening", f"HTTP listening on {host}:{port} auth={'password' if app.state.login_required else 'none (loopback only)'}", host=host, port=port)
    uvicorn.run(app, host=host, port=port, proxy_headers=False, server_header=False, date_header=False, limit_concurrency=MAX_CONNECTIONS,
                timeout_keep_alive=KEEP_ALIVE_SECONDS, log_config=None, access_log=False)


def trusted_proxies():
    try:
        return [ipaddress.ip_network(item.strip(), strict=False) for item in os.environ.get("SUBTITLE_GUARD_TRUSTED_PROXIES", "").split(",") if item.strip()]
    except ValueError as error:
        raise ConfigError(f"SUBTITLE_GUARD_TRUSTED_PROXIES must list addresses or networks separated by commas: {error}") from error


async def http_problem(request: Request, error: HTTPException):
    detail = error.detail if isinstance(error.detail, dict) else {"code": "error", "message": str(error.detail)}
    return JSONResponse(detail, status_code=error.status_code, headers={**SECURITY_HEADERS, **(error.headers or {})})


async def invalid_request(request: Request, error: RequestValidationError):
    fields = [".".join(str(part) for part in item["loc"][1:]) for item in error.errors()]
    return JSONResponse({"code": "invalid_request", "message": tr("Invalid field: {fields}.", fields=", ".join(fields) or "body")}, status_code=422, headers=SECURITY_HEADERS)
