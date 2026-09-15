"""Serve the production frontend build from the supervised backend.

WHY HERE
--------
After the 2026-09-15 reboot the backend came back on its own and the frontend
did not: it only ever ran as ``npm run dev``, which nothing supervises. Rather
than add a second process with its own supervisor, its own port owner and its
own way to fail, the already-supervised backend serves the built files. One
owner, one recovery path: when "ASP Backend 8010" comes back, so does the app.

WHAT IT CANNOT SHADOW
---------------------
The API, the health probes and the API docs. The SPA fallback is registered
last and explicitly refuses those prefixes, so a missing API route still
returns the API's own 404 rather than an HTML page that looks like success.

WHAT IT DOES NOT CARRY
----------------------
Secrets. A Vite build embeds only VITE_-prefixed variables, and the only one is
the API base, built as the same-origin ``/api/v1``.

LOCAL DEVELOPMENT
-----------------
Unchanged. ``npm run dev`` still serves hot-reloading source on 5173 against
this backend. The build served here is a separate, explicit artifact, and
``/frontendz`` reports exactly which one is live.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

#: Paths the SPA fallback must never answer.
RESERVED_PREFIXES = ("api/", "api", "healthz", "readyz", "frontendz", "docs", "redoc", "openapi.json")


def default_dist() -> Path:
    override = os.environ.get("AISP_FRONTEND_DIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "client" / "dist"


def build_identity(dist: Path) -> dict[str, Any]:
    index = dist / "index.html"
    if not index.is_file():
        return {"present": False, "dist": str(dist),
                "detail": "No production build is present; run the frontend build."}
    identity_file = dist / "build-identity.json"
    identity: dict[str, Any] = {}
    if identity_file.is_file():
        try:
            identity = json.loads(identity_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            identity = {"unreadable": True}
    return {
        "present": True,
        "dist": str(dist),
        "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
        "assets": len(list((dist / "assets").glob("*"))) if (dist / "assets").is_dir() else 0,
        "build": identity,
    }


def mount_frontend(application: FastAPI, dist: Path | None = None) -> None:
    """Register the probe, the hashed assets and the SPA fallback -- in that order, last."""

    root = dist or default_dist()

    @application.get("/frontendz", include_in_schema=False)
    def frontendz() -> JSONResponse:
        identity = build_identity(root)
        return JSONResponse(status_code=200 if identity["present"] else 503,
                            content={"status": "ready" if identity["present"] else "no_build", **identity})

    assets = root / "assets"
    if assets.is_dir():
        # Content-hashed file names: safe to cache for a long time.
        application.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @application.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith(RESERVED_PREFIXES):
            raise HTTPException(status_code=404)
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail="No production frontend build is present.")
        candidate = (root / full_path).resolve()
        # A real top-level file (favicon, manifest) is served as itself; anything
        # that escapes the build directory is refused rather than resolved.
        if full_path and candidate.is_file() and root.resolve() in candidate.parents:
            return FileResponse(candidate)
        # index.html must never be cached: it names the hashed assets, and a
        # stale copy would load a build that no longer exists.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


__all__ = ["RESERVED_PREFIXES", "build_identity", "default_dist", "mount_frontend"]
