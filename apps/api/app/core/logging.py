"""Structured logging with request/job correlation.

Secrets are never logged. Values that look like credentials are redacted by
``structlog``'s processor chain.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import settings

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)
job_id_ctx: ContextVar[str | None] = ContextVar("job_id", default=None)
document_id_ctx: ContextVar[str | None] = ContextVar("document_id", default=None)

_SECRET_HINTS = ("password", "secret", "token", "api_key", "apikey", "authorization", "cookie")

_CONFIGURED = False


def _redact(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        lowered = key.lower()
        if any(hint in lowered for hint in _SECRET_HINTS):
            event_dict[key] = "***redacted***"
    return event_dict


def _inject_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for name, ctx in (
        ("request_id", request_id_ctx),
        ("user_id", user_id_ctx),
        ("job_id", job_id_ctx),
        ("document_id", document_id_ctx),
    ):
        value = ctx.get()
        if value and name not in event_dict:
            event_dict[name] = value
    return event_dict


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for noisy in ("uvicorn.access", "neo4j", "urllib3", "botocore", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _inject_context,
            _redact,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)  # type: ignore[return-value]


class bind_context:
    """Context manager binding correlation ids for the duration of a block."""

    def __init__(self, **values: str | None) -> None:
        self._values = values
        self._tokens: list[tuple[ContextVar[str | None], Any]] = []

    def __enter__(self) -> "bindContext":
        for name, value in self._values.items():
            ctx = {
                "request_id": request_id_ctx,
                "user_id": user_id_ctx,
                "job_id": job_id_ctx,
                "document_id": document_id_ctx,
            }.get(name)
            if ctx is not None:
                self._tokens.append((ctx, ctx.set(value)))
        return self

    def __exit__(self, *exc: object) -> None:
        for ctx, token in reversed(self._tokens):
            ctx.reset(token)
        self._tokens.clear()


logger = get_logger("dha")
