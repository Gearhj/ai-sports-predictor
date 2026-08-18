from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, hsts: bool = False) -> None:
        super().__init__(app)
        # HSTS is only meaningful over HTTPS and must never be emitted for
        # plaintext local development. It is enabled only when the deployment
        # boundary guarantees TLS (production).
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Swagger/ReDoc load their own assets; JSON API responses need no active
        # content and receive the strictest policy.
        if request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if self.hsts:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
