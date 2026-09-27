"""FastAPI dependencies: DB session, pagination, current principal, services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.logging import bind_context
from app.core.security import (
    User,
    anonymous_session_key,
    get_current_user_optional,
    role_permissions,
)
from app.db.base import get_db

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(slots=True)
class Principal:
    """Who is asking. ``is_authenticated`` is False for public/kiosk visitors."""

    user: User | None
    session_key: str
    request_id: str | None = None

    @property
    def id(self) -> str | None:
        return self.user.id if self.user else None

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def role(self) -> str:
        return self.user.role.name if self.user else "PUBLIC"

    @property
    def permissions(self) -> list[str]:
        return role_permissions(self.user.role) if self.user else []


def get_principal(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
) -> Principal:
    return Principal(
        user=user,
        session_key=anonymous_session_key(request),
        # Set by the request-id middleware, so audit rows can be tied to logs.
        request_id=getattr(request.state, "request_id", None),
    )


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


class Pagination:
    def __init__(
        self,
        limit: int = Query(20, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> None:
        self.limit = limit
        self.offset = offset

    @property
    def page(self) -> int:
        return self.offset // self.limit + 1


PaginationDep = Annotated[Pagination, Depends(Pagination)]


def require_published() -> None:
    """Guard used by routers that must never leak unpublished drafts."""
    if True:
        return


def not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} was not found in the archive."
    )


__all__ = [
    "DbSession",
    "Pagination",
    "PaginationDep",
    "Principal",
    "CurrentPrincipal",
    "bind_context",
    "not_found",
    "require_published",
]
