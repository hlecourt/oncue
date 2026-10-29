# SPDX-License-Identifier: GPL-3.0-or-later
"""Log in, log out, and the session state with its CSRF token for the front."""

import threading

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from ...logs import log
from ..auth import LOCKOUT_SECONDS, SESSION_SECONDS, SESSIONS, locked_out, open_session, record_failure, verify
from ..i18n import tr
from ..schemas import Credentials, Problem, SessionState
from ..security import SESSION_COOKIE, client_address, current_session, login_needed, require_csrf

router = APIRouter(prefix="/session", tags=["session"])
LOGIN_LOCK = threading.Lock()


@router.get("", operation_id="getSession")
def get_session(request: Request) -> SessionState:
    session = current_session(request)
    if session:
        return SessionState(login_required=True, user=session["user"], csrf=session["csrf"])
    if login_needed(request):
        return SessionState(login_required=True, user=None, csrf=None)
    return SessionState(login_required=False, user=None, csrf=request.app.state.anonymous_csrf)


@router.post("", operation_id="logIn", responses={401: {"model": Problem}, 429: {"model": Problem}})
def log_in(credentials: Credentials, request: Request) -> SessionState:
    client = client_address(request)
    with LOGIN_LOCK:
        if locked_out(client):
            return problem(429, "locked_out", tr("Too many wrong attempts. Wait {minutes} minutes and try again.", minutes=LOCKOUT_SECONDS // 60))
        if not verify(credentials.user, credentials.password):
            record_failure(client)
            return problem(401, "wrong_credentials", tr("That user name or password is not right."))
    session = open_session(credentials.user)
    log("api.login", f"user {credentials.user} logged in from {client}", user=credentials.user, client=client)
    response = JSONResponse(SessionState(login_required=True, user=credentials.user, csrf=SESSIONS[session]["csrf"]).model_dump())
    response.set_cookie(SESSION_COOKIE, session, max_age=SESSION_SECONDS, httponly=True, samesite="strict", secure=secure(request))
    return response


@router.delete("", operation_id="logOut", status_code=204, dependencies=[Depends(require_csrf)])
def log_out(request: Request) -> Response:
    SESSIONS.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE)
    return response


def secure(request):
    return request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"


def problem(status, code, message):
    return JSONResponse({"code": code, "message": message}, status_code=status)
