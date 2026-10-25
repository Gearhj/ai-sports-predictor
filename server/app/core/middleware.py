from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


REPORT_FRAME_HEADER = "X-AISP-Frame-Policy"
#: Static HTML report pages: inline styles and same-origin images only; no script, no form, framed only by this origin.
REPORT_PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; "
                   "form-action 'none'; frame-ancestors 'self'")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, hsts: bool = False) -> None:
        super().__init__(app)
        # HSTS is only meaningful over HTTPS and must never be emitted for
        # plaintext local development. It is enabled only when the deployment
        # boundary guarantees TLS (production).
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        # A read-only HTML report page that the site embeds in its own page (same origin only) opts in with
        # REPORT_FRAME_HEADER; it gets inline styles and same-origin framing, never scripts or other origins.
        report_page = response.headers.get(REPORT_FRAME_HEADER) == "same-origin-report"
        if report_page:
            del response.headers[REPORT_FRAME_HEADER]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN" if report_page else "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Swagger/ReDoc load their own assets; JSON API responses need no active
        # content and receive the strictest policy.
        if report_page:
            response.headers["Content-Security-Policy"] = REPORT_PAGE_CSP
        elif request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if self.hsts:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
