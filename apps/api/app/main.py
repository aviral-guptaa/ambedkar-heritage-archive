"""FastAPI application entry point.

Two things matter here beyond wiring routers together:

* every response carries a request id and a server-timing header, so a slow
  search or RAG answer can be diagnosed from the outside;
* nothing in this app pretends a capability exists. If OCR, Neo4j, speech or
  translation is missing, /health and /system/capabilities say so, and the
  corresponding feature is reported as unavailable instead of failing silently.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.api import (
    admin,
    auth,
    digitize,
    documents,
    graph,
    media,
    rag,
    search,
    speech,
    system,
)
from app.core.config import settings
from app.core.logging import get_logger, configure_logging
from app.core.production_guard import assert_production_ready
from app.core.spa import mount_interface
from app.providers.graph import graph_capability
from app.providers.jobs import queue_capability
from app.providers.storage import storage_capability

log = get_logger(__name__)

RATE_LIMIT_WINDOW_SECONDS = 60


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request id, structured access log and response timing."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = str(elapsed_ms)
        if request.url.path.startswith(settings.api_prefix):
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=elapsed_ms,
                request_id=request_id,
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-process limiter.

    It is a guard against a runaway client on a single kiosk, not a substitute
    for a shared limiter: with more than one API process, use Redis.
    """

    def __init__(self, app: FastAPI) -> None:  # noqa: ANN001
        super().__init__(app)
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.exempt = {"/api/v1/health", "/api/v1/system/capabilities", "/docs", "/openapi.json"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in self.exempt or request.method == "OPTIONS":
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        limit = (
            settings.auth_rate_limit_per_minute
            if "/auth/login" in request.url.path
            else settings.rate_limit_per_minute
        )
        now = time.monotonic()
        window = self.hits[client]
        while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Too many requests. Please slow down and try again shortly."
                },
                headers={"retry-after": str(RATE_LIMIT_WINDOW_SECONDS)},
            )
        window.append(now)
        if len(self.hits) > 4096:  # keep the map from growing without bound
            for key in list(self.hits)[:2048]:
                if not self.hits[key]:
                    del self.hits[key]
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    configure_logging()
    # Before the first request rather than on the first request: a deployment
    # that is unsafe should not answer anything at all, not even a health check.
    assert_production_ready(settings)
    log.info(
        "starting",
        environment=settings.environment,
        database=settings.database_url.split("@")[-1],
        storage=storage_capability().get("provider"),
        queue=queue_capability().get("backend"),
        graph=graph_capability().get("backend"),
    )
    yield
    log.info("stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=system.APP_VERSION,
        description=(
            "Evidence-grounded digital heritage archive for the works and life of "
            "Dr. B. R. Ambedkar. Every answer is derived from indexed sources and "
            "cites them; the API refuses rather than inventing."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id", "x-response-time-ms", "content-range"],
    )

    prefix = settings.api_prefix
    for module in (system, auth, documents, digitize, search, rag, graph, media, speech, admin):
        app.include_router(module.router, prefix=prefix)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Return field-level messages the UI can show next to the input."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Some fields need attention.",
                "errors": [
                    {
                        "field": ".".join(str(p) for p in err.get("loc", [])[1:]) or "body",
                        "message": err.get("msg", "invalid value"),
                    }
                    for err in exc.errors()
                ],
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "request_id": getattr(request.state, "request_id", None),
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak a stack trace or a database error string to a visitor.
        log.error(
            "unhandled error",
            path=request.url.path,
            error=str(exc),
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": (
                    "The archive hit an unexpected error handling this request. "
                    "Nothing was changed. Please try again."
                ),
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    # Serving the interface from the API is opt-in, and only for deployments
    # that expose a single port. A kiosk keeps nginx in front; see app/core/spa.py.
    web_dist = settings.web_dist
    interface_mounted = mount_interface(app, web_dist, prefix) if web_dist else False

    if not interface_mounted:

        @app.get("/", include_in_schema=False)
        def root() -> dict[str, Any]:
            return {
                "service": settings.app_name,
                "version": system.APP_VERSION,
                "api": prefix,
                "docs": "/docs",
                "health": f"{prefix}/health",
            }

    return app


app = create_app()
