# SPDX-License-Identifier: GPL-3.0-or-later
"""Serve the built web front: hashed assets cached for good, every other page answered with index.html under the URL base."""

import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

STATIC_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
FOREVER = "public, max-age=31536000, immutable"
NOT_BUILT = ("<!doctype html><title>subtitle-guard</title><p>The web front is not built. Install a release, or run <code>pnpm build</code> in "
             "<code>frontend/</code>.</p>")


def add_front(app: FastAPI, base: str):
    @app.get(f"{base}/{{page:path}}", include_in_schema=False)
    def front(page: str, request: Request):
        if page == "api" or page.startswith("api/"):
            return JSONResponse({"code": "not_found", "message": "There is no such API route."}, status_code=404)
        asset = os.path.realpath(os.path.join(STATIC_FOLDER, page))
        if page and asset.startswith(STATIC_FOLDER + os.sep) and os.path.isfile(asset):
            headers = {"Cache-Control": FOREVER} if page.startswith("assets/") else {}
            return FileResponse(asset, headers=headers)
        if page.startswith("assets/") or "text/html" not in request.headers.get("accept", "text/html"):
            return JSONResponse({"code": "not_found", "message": "Not found."}, status_code=404)
        index = os.path.join(STATIC_FOLDER, "index.html")
        if not os.path.isfile(index):
            return HTMLResponse(NOT_BUILT, status_code=503)
        with open(index, encoding="utf-8") as handle:
            return HTMLResponse(handle.read().replace('<base href="/" />', f'<base href="{base}/" />', 1))
