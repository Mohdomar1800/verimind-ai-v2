"""FastAPI application factory.

The same app serves both the HTML user interface (routes/pages.py) and the JSON API (/api/...).
Run locally with:  uvicorn verimind.web.app:create_app --factory --reload
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from verimind import __version__
from verimind.config import get_settings
from verimind.logging_setup import configure_logging
from verimind.storage import StorageError
from verimind.web.routes import api_results, api_runs, health, pages, pages_results
from verimind.web.security import security_middleware
from verimind.web.templating import WEB_DIR, render_page

API_DESCRIPTION = """
Triage RTL verification logs into ranked root-cause issues.

Typical flow: **upload** logs (`POST /api/runs`), poll **progress** until the run is `done`,
then read the ranked **issues**, drill into **tests** and **logs**, and **compare** runs.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="VeriMind",
        version=__version__,
        description=API_DESCRIPTION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # Reject oversized uploads before the body is read (the storage layer also enforces the limit).
    max_body = (settings.max_upload_mb + 1) * 1024**2

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_body:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Upload is larger than the limit of {settings.max_upload_mb:,} MB."},
            )
        return await call_next(request)

    app.middleware("http")(security_middleware(settings.allowed_origins))

    # HTML forms send "issue=" for "Any"; treat blank query values as not given instead of a 422.
    @app.middleware("http")
    async def drop_blank_query_params(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        query = request.scope.get("query_string", b"")
        if query:
            kept = [(k, v) for k, v in parse_qsl(query.decode("latin-1"), keep_blank_values=True) if v.strip()]
            request.scope["query_string"] = urlencode(kept).encode("latin-1")
        return await call_next(request)

    @app.exception_handler(StorageError)
    async def storage_error(_request: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def missing_file(_request: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    # Pages get friendly HTML errors; the API keeps JSON errors.
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/api") or exc.status_code < 400:
            return await http_exception_handler(request, exc)
        heading = "Page not found" if exc.status_code == 404 else "Something went wrong"
        message = str(exc.detail)
        if exc.status_code == 404 and message == "Not Found":  # Starlette's default: unknown URL
            message = "There is nothing at this address. It may have been deleted."
        return render_page(request, "error.html", exc.status_code, heading=heading, message=message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> Response:
        if request.url.path.startswith("/api"):
            return await request_validation_exception_handler(request, exc)
        message = "This address has a value VeriMind doesn't understand. Go back and try again."
        return render_page(request, "error.html", 400, heading="Something went wrong", message=message)

    @app.exception_handler(OperationalError)
    async def database_down(request: Request, _exc: OperationalError) -> Response:
        message = "VeriMind can't reach its database. If you use Docker, check that all containers are running."
        if request.url.path.startswith("/api"):
            return JSONResponse(status_code=503, content={"detail": message})
        return render_page(request, "error.html", 503, heading="Database unavailable", message=message)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(health.router)
    app.include_router(api_runs.router)
    app.include_router(api_results.router)
    app.include_router(pages.router)
    app.include_router(pages_results.router)
    return app
