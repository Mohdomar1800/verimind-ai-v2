"""HTTP security: response headers and a cross-site request check.

- **Content Security Policy:** pages may only run scripts served by VeriMind itself (no inline
  scripts, no CDNs), which blunts cross-site scripting. Inline `style="width: 40%"` attributes are
  still allowed because the charts and progress bars use them. The interactive API docs page loads
  Swagger UI from a CDN, so it gets its own policy.
- **Cross-site request check:** a browser always sends an `Origin` header on cross-site form posts
  and fetches. Requests that change data (POST/PUT/PATCH/DELETE) are refused when that origin isn't
  VeriMind's own, so another website can't make a visitor's browser delete runs or edit triage.
  Scripts and `curl` send no `Origin` and are unaffected.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse

APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DOCS_PATHS = ("/api/docs", "/api/redoc")

Handler = Callable[[Request], Awaitable[Response]]


def is_same_origin(request: Request, extra_allowed: list[str]) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # not a browser cross-site request (curl, scripts, same-origin GET-initiated forms)
    if origin in extra_allowed:
        return True
    return urlsplit(origin).netloc.lower() == (request.headers.get("host") or "").lower()


def security_middleware(extra_allowed_origins: list[str]) -> Callable[[Request, Handler], Awaitable[Response]]:
    async def middleware(request: Request, call_next: Handler) -> Response:
        if request.method in UNSAFE_METHODS and not is_same_origin(request, extra_allowed_origins):
            return JSONResponse(status_code=403, content={"detail": "Cross-site request blocked."})
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = DOCS_CSP if request.url.path.startswith(DOCS_PATHS) else APP_CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    return middleware
