"""Create or update an archive account.

Idempotent: running it again with the same email updates the name, role and
password rather than creating a duplicate. Needed on first deployment, because
the archive ships no accounts and the public API is readable without signing in.

    PYTHONPATH=. python scripts/create_user.py \
        --email archivist@example.org --role admin --password '...'

The password can also be supplied through DHA_ADMIN_PASSWORD, so it does not
have to appear in the process list.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Sequence

from sqlalchemy import select


# Allow "python scripts/<name>.py" from anywhere, which is how the README
# documents these commands, without requiring PYTHONPATH to be set first.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from app.core.logging import configure_logging
from app.core.security import hash_password
from app.db.base import session_scope
from app.models.access import DEFAULT_ROLE_PERMISSIONS, Role, User
from app.models.enums import UserRole

# Roles and their permissions are defined by the application itself, so the CLI
# cannot drift away from what the API actually enforces.
ROLE_DESCRIPTIONS = {
    UserRole.SUPER_ADMIN.value: "Full access, including user management and integrity checks.",
    UserRole.ARCHIVIST.value: "Curate the whole archive and verify provenance.",
    UserRole.EDITOR.value: "Prepare documents, media, stories and translations for review.",
    UserRole.RESEARCHER.value: "Run OCR and contribute graph data under supervision.",
    UserRole.VIEWER.value: "Read-only access for review and demonstration.",
}


def ensure_role(db, name: str) -> Role:
    if name not in DEFAULT_ROLE_PERMISSIONS:
        raise ValueError(f"Unknown role {name!r}; expected one of {sorted(DEFAULT_ROLE_PERMISSIONS)}.")
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:
        role = Role(
            name=name,
            description=ROLE_DESCRIPTIONS.get(name),
            permissions=list(DEFAULT_ROLE_PERMISSIONS[name]),
        )
        db.add(role)
        db.flush()
    else:
        # A role that predates a permission change would silently lock operators
        # out, so keep the stored grants in step with the application definition.
        # This widens only: an operator's extra grants are preserved.
        current = set(role.permissions or [])
        role.permissions = sorted(current | set(DEFAULT_ROLE_PERMISSIONS[name]))
    return role


def upsert_user(
    db,
    *,
    email: str,
    full_name: str,
    password: str,
    role_name: str,
    institution: str | None = None,
    active: bool = True,
) -> tuple[User, bool]:
    role = ensure_role(db, role_name)
    user = db.scalar(select(User).where(User.email == email.lower()))
    created = user is None
    if created:
        user = User(email=email.lower(), full_name=full_name, role_id=role.id)
        db.add(user)
    else:
        user.full_name = full_name
        user.role_id = role.id
    if institution is not None:
        user.institution = institution
    user.is_active = active
    user.is_demo = False
    # Rotating token_version invalidates any JWT already issued for this account.
    user.token_version = (user.token_version or 0) + 1
    user.password_hash = hash_password(password)
    db.flush()
    return user, created


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None, help="Full name (defaults to the email).")
    parser.add_argument(
        "--role",
        default=UserRole.VIEWER.value,
        choices=sorted(DEFAULT_ROLE_PERMISSIONS),
        help="Role to grant.",
    )
    parser.add_argument("--institution", default=None)
    parser.add_argument("--password", default=None, help="Prompted for if omitted.")
    args = parser.parse_args(argv)

    password = args.password or __import__("os").environ.get("DHA_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Confirm password: "):
            print("Passwords did not match.", file=sys.stderr)
            return 2
    if len(password) < 12:
        print("Refusing a password shorter than 12 characters.", file=sys.stderr)
        return 2

    configure_logging()
    with session_scope() as db:
        user, created = upsert_user(
            db,
            email=args.email,
            full_name=args.name or args.email,
            password=password,
            role_name=args.role,
            institution=args.institution,
        )
        print(f"{'Created' if created else 'Updated'} {user.email} with role {args.role}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
