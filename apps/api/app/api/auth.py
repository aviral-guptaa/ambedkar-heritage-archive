"""Authentication and user administration.

The public site and kiosk are anonymous. Only the separate admin application
authenticates, and every privileged route re-checks a permission server-side:
hiding a button in the UI is never treated as access control.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentPrincipal, DbSession
from app.core.logging import get_logger
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    password_policy_error,
    require_permission,
    role_permissions,
    verify_password,
)
from app.models.access import DEFAULT_ROLE_PERMISSIONS, Role, User
from app.models.enums import UserRole as UserRoleEnum
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserCreateRequest,
    UserResponse,
)

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        expires_in=settings.access_token_minutes * 60,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.name,
            is_active=user.is_active,
            created_at=user.created_at,
        ),
    )


def _resolve_role(db: Session, role: UserRoleEnum) -> Role:
    """Return the database role row, creating the built-in roles on demand."""
    row = db.scalar(select(Role).where(Role.name == role.value))
    if row is None:
        row = Role(
            name=role.value,
            description=f"Built-in {role.value} role",
            permissions=list(DEFAULT_ROLE_PERMISSIONS.get(role.value, [])),
        )
        db.add(row)
        db.flush()
    return row


@router.post("/login", response_model=TokenResponse, summary="Sign in to the admin application")
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    email = payload.email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        # Identical message and roughly identical work either way, so the
        # response does not reveal whether an account exists.
        log.warning("failed login", email=email, client=request.client.host if request.client else None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated. Contact an administrator.",
        )
    user.last_login_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)
    log.info("login", user_id=user.id, role=user.role.name)
    return _tokens(user)


@router.post("/refresh", response_model=TokenResponse, summary="Exchange a refresh token")
def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = db.get(User, claims.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is no longer valid."
        )
    return _tokens(user)


@router.get("/me", response_model=UserResponse, summary="Current user")
def me(user=Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.get("/permissions", summary="Permissions granted to the current role")
def permissions(principal: CurrentPrincipal) -> dict[str, object]:
    return {
        "role": principal.role,
        "authenticated": principal.is_authenticated,
        "permissions": principal.permissions,
    }


@router.post("/change-password", summary="Change your own password")
def change_password(
    payload: ChangePasswordRequest,
    db: DbSession,
    user=Depends(get_current_user),
) -> dict[str, str]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect."
        )
    problem = password_policy_error(payload.new_password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The new password must be different from the current one.",
        )
    user.password_hash = hash_password(payload.new_password)
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    log.info("password changed", user_id=user.id)
    return {"detail": "Password updated. Existing sessions remain valid until expiry."}


# --------------------------------------------------------------------------- #
# administration
# --------------------------------------------------------------------------- #


@router.get("/users", response_model=list[UserResponse], summary="List users")
def list_users(
    db: DbSession, _: object = Depends(require_permission("user:read"))
) -> list[UserResponse]:
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return [
        UserResponse(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role.name,
            is_active=u.is_active,
            created_at=u.created_at,
        )
        for u in users
    ]


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
)
def create_user(
    payload: UserCreateRequest,
    db: DbSession,
    _: object = Depends(require_permission("user:read")),
) -> UserResponse:
    email = payload.email.strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account with that email already exists."
        )
    problem = password_policy_error(payload.password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
    try:
        role = UserRoleEnum(payload.role.upper())
    except ValueError as exc:
        allowed = ", ".join(r.value for r in UserRoleEnum)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown role '{payload.role}'. Allowed: {allowed}.",
        ) from exc
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=(payload.full_name or email.split("@")[0]).strip(),
        role=_resolve_role(db, role),
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # pragma: no cover - race
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use.") from exc
    db.refresh(user)
    log.info("user created", user_id=user.id, role=role.value, by=payload.email)
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/users/{user_id}/deactivate", summary="Deactivate a user")
def deactivate_user(
    user_id: str,
    db: DbSession,
    actor=Depends(get_current_user),
    _: object = Depends(require_permission("user:read")),
) -> dict[str, str]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if user.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate your own account."
        )
    user.is_active = False
    db.commit()
    log.info("user deactivated", user_id=user.id, by=actor.id)
    return {"detail": f"{user.email} can no longer sign in."}


@router.get("/roles", summary="Roles and their permissions")
def list_roles(db: DbSession) -> list[dict[str, object]]:
    rows = db.scalars(select(Role)).all()
    known = {r.name: r for r in rows}
    out: list[dict[str, object]] = []
    for role in UserRoleEnum:
        row = known.get(role.value)
        out.append(
            {
                "name": role.value,
                "description": row.description if row else None,
                "permissions": role_permissions(row or role),
            }
        )
    return out
