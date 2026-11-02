"""Request-body size cap, enforced before any route reads or parses a body.

WHY (security review 11-01: unbounded JSON body parse on the event loop)
-----------------------------------------------------------------------
FastAPI reads and ``json.loads`` a ``Body(...)`` parameter on the event loop
before any length check inside the route can run, so one multi-megabyte POST
stalls every other request. The per-route checks in ``routes.py`` and
``preview_feeds_routes.py`` only run after the whole body is in memory.

WHAT IT DOES
------------
A pure ASGI middleware, installed last in ``create_app`` so it wraps every
user middleware. For any HTTP request other than GET/HEAD:

* a ``Content-Length`` over the path's cap is answered 413 without calling the
  app (a malformed ``Content-Length`` is answered 400);
* every body (chunked or not) is counted as it is received; past the cap the
  app is told the client disconnected, whatever it then sends is discarded, and
  the client gets 413.

Caps sit above each route's own limit, so no request a route accepts today is
refused here: arena sync accepts up to 400 000 bytes (cap 512 KiB), the Booth
65 536 (cap 64 KiB), probability evaluate 65 536 re-serialized (cap 128 KiB);
everything else takes small pydantic bodies (default 64 KiB).
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_MAX_BODY_BYTES = 64 * 1024
#: Exact paths (after the API prefix is substituted) with their own cap.
PATH_MAX_BODY_BYTES: dict[str, int] = {
    "/preview-api/arena/sync": 512 * 1024,
    "/preview-api/booth": 64 * 1024,
    "{api}/probability/evaluate": 128 * 1024,
}
#: Path prefixes with their own cap (the account's store copies: one store is at most 1 MB, see services/user_state.py).
PREFIX_MAX_BODY_BYTES: dict[str, int] = {
    "{api}/me/state/": 1_100_000,
}
UNLIMITED_METHODS = frozenset({"GET", "HEAD"})


class _Response:
    @staticmethod
    async def send(send: Any, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"connection", b"close"),
        ]})
        await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    def __init__(self, app: Any, *, api_prefix: str = "/api/v1", default_max: int = DEFAULT_MAX_BODY_BYTES,
                 path_caps: dict[str, int] | None = None) -> None:
        self.app = app
        self.default_max = default_max
        caps = PATH_MAX_BODY_BYTES if path_caps is None else path_caps
        self.caps = {path.replace("{api}", api_prefix.rstrip("/")): cap for path, cap in caps.items()}
        self.prefix_caps = {path.replace("{api}", api_prefix.rstrip("/")): cap for path, cap in PREFIX_MAX_BODY_BYTES.items()}

    def cap_for(self, path: str) -> int:
        exact = self.caps.get(path.rstrip("/") or "/", self.caps.get(path))
        if exact is not None:
            return exact
        for prefix, cap in self.prefix_caps.items():
            if path.startswith(prefix):
                return cap
        return self.default_max

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method", "GET").upper() in UNLIMITED_METHODS:
            await self.app(scope, receive, send)
            return
        cap = self.cap_for(scope.get("path", ""))
        declared = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared = int(value.decode("latin-1").strip())
                except (ValueError, UnicodeDecodeError):
                    await _Response.send(send, 400, "Invalid Content-Length.")
                    return
                if declared < 0:
                    await _Response.send(send, 400, "Invalid Content-Length.")
                    return
        if declared is not None and declared > cap:
            await _Response.send(send, 413, "Request too large.")
            return

        state = {"received": 0, "too_large": False, "started": False, "answered": False}

        async def limited_receive() -> dict[str, Any]:
            if state["too_large"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                state["received"] += len(message.get("body", b"") or b"")
                if state["received"] > cap:
                    state["too_large"] = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: dict[str, Any]) -> None:
            if state["too_large"] and not state["started"]:
                return  # the app's answer to a truncated body is replaced by the 413 below
            if message.get("type") == "http.response.start":
                state["started"] = True
            await send(message)

        async def answer_413() -> None:
            if not state["answered"] and not state["started"]:
                state["answered"] = True
                await _Response.send(send, 413, "Request too large.")

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if state["too_large"]:
                await answer_413()
                return
            raise
        if state["too_large"]:
            await answer_413()


__all__ = ["BodyLimitMiddleware", "DEFAULT_MAX_BODY_BYTES", "PATH_MAX_BODY_BYTES"]
