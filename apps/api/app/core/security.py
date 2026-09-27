"""Authentication, password hashing and server-side permission enforcement."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import get_db
from app.models.access import Role, User
from app.models.enums import UserRole

log = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

TOKEN_TYPE = "bearer"


class TokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def password_policy_error(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters long."
    if not any(c.isalpha() for c in password):
        return "Password must contain at least one letter."
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one digit."
    return None


def _create_token(subject: str, role: str, token_type: str, expires: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
        "jti": secrets.token_hex(8),
        "iss": settings.app_slug,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user: User) -> str:
    return _create_token(
        user.id,
        user.role.name,
        "access",
        timedelta(minutes=settings.access_token_minutes),
    )


def create_refresh_token(user: User) -> str:
    return _create_token(
        user.id,
        user.role.name,
        "refresh",
        timedelta(days=settings.refresh_token_days),
    )


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_slug,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Session expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Invalid authentication token.") from exc
    if payload.get("type") != expected_type:
        raise TokenError("Invalid token type.")
    return payload


def role_permissions(role: Role | UserRole | str) -> list[str]:
    if isinstance(role, Role):
        return list(role.permissions or [])
    from app.models.access import DEFAULT_ROLE_PERMISSIONS

    return list(DEFAULT_ROLE_PERMISSIONS.get(str(role), []))


def has_permission(user: User, permission: str) -> bool:
    perms = role_permissions(user.role)
    return "*" in perms or permission in perms


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    if credentials is None or not credentials.credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except TokenError:
        return None
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    user: User | None = Depends(get_current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": TOKEN_TYPE},
        )
    return user


def require_permission(permission: str):  # noqa: ANN201 - FastAPI dependency factory
    """Server-side authorisation. Never rely on the UI hiding a control."""

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user, permission):
            log.warning(
                "permission denied", user_id=user.id, permission=permission, role=user.role.name
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role ({user.role.name}) is not permitted to perform this action.",
            )
        return user

    return _dependency


def require_any_permission(*permissions: str):  # noqa: ANN201
    """Dependency factory: authenticated, and holding at least one permission.

    Used as a router-level gate. Per-handler checks then narrow to the specific
    permission, but a handler that forgets one fails closed instead of exposing
    the admin surface to the public.
    """

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if not any(has_permission(user, permission) for permission in permissions):
            log.warning(
                "permission denied",
                user_id=user.id,
                permission="|".join(permissions),
                role=user.role.name,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role ({user.role.name}) cannot access this area.",
            )
        return user

    return _dependency


def require_role(*roles: UserRole):  # noqa: ANN201
    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role.name not in {r.value for r in roles}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of: {', '.join(r.value for r in roles)}.",
            )
        return user

    return _dependency


def anonymous_session_key(request: Request) -> str:
    """Stable per-device key so kiosk visitors can keep a research collection."""
    key = request.headers.get("X-Session-Key")
    if key and 8 <= len(key) <= 64 and key.isalnum():
        return key
    return f"anon-{_client_ip_hash(request)}"


def _client_ip_hash(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    import hashlib

    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]


def load_role(db: Session, name: str) -> Role | None:
    return db.scalar(select(Role).where(Role.name == name))
