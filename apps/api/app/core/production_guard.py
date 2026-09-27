"""Refusing to start a public deployment on development settings.

A hosted archive is a public URL. Three defaults in this project are fine on a
laptop and unacceptable in production:

* the JWT signing secret is a known string, so anyone could mint an archivist's
  session;
* debug mode returns internals in errors;
* ``demo_mode`` replaces real records with invented ones, which for this project
  is the one failure that matters most.

Each is a plausible mistake: a new environment with an incomplete variable list
inherits the default silently, and the archive comes up looking healthy. A
deployment that refuses to start is inconvenient. A deployment serving forged
archivist sessions is a security incident, and one quietly serving invented
records defeats the entire project.

So this runs at import time and stops the process. It is a hard failure on
purpose, and the message says exactly which variable to set.
"""

from __future__ import annotations

from app.core.config import Settings

#: The value shipped in the source. Public, therefore not a secret.
INSECURE_JWT_SECRET = "dev-only-insecure-secret-change-me"
WEAK_JWT_SECRETS = {"", "change-me", "secret", "please-change-me"}


def check_production_settings(settings: Settings) -> list[str]:
    """Return the reasons this configuration must not be deployed."""
    problems: list[str] = []

    if not settings.is_production:
        return problems

    secret = settings.jwt_secret.strip()
    if secret == INSECURE_JWT_SECRET or secret.lower() in WEAK_JWT_SECRETS:
        problems.append(
            "JWT_SECRET is still the development default. Anyone could mint an "
            "archivist session. Generate one with: "
            "python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    elif len(secret) < 32:
        problems.append(
            "JWT_SECRET is shorter than 32 characters, which is too short to be "
            "worth brute-forcing for an HMAC key."
        )

    if settings.debug:
        problems.append(
            "DEBUG is true, so error responses include internals. Set DEBUG=false."
        )

    if settings.demo_mode:
        problems.append(
            "DEMO_MODE is true. This archive is about not inventing records, so "
            "serving invented ones, even labelled, is not a mode to deploy. "
            "Set DEMO_MODE=false."
        )

    if settings.database_url.startswith("sqlite"):
        problems.append(
            "DATABASE_URL points at SQLite, which cannot render the full-text "
            "and vector types this schema uses. The archive would start and then "
            "fail on the first search."
        )

    if not settings.cors_origins:
        # Deliberately not a problem. Both supported arrangements serve the
        # interface and the API from one origin — nginx proxies /api for the
        # kiosk, and WEB_DIST makes FastAPI serve the built interface for a
        # single-port deployment — so a browser never makes a cross-origin
        # request and no CORS headers are needed at all. Requiring a value here
        # would have blocked the one-port deployment it was meant to protect.
        pass
    elif "*" in settings.cors_origins:
        # CORS is applied with allow_credentials=True. A wildcard alongside
        # credentials is refused by browsers and means the intent was probably
        # to list origins, not to allow all of them.
        problems.append(
            "CORS_ORIGINS contains '*', and credentials are enabled. Browsers "
            "reject that combination, so cross-origin sign-in would fail. List "
            "the origins explicitly — or set CORS_ORIGINS=[] if the interface and "
            "the API share an origin, which is the normal arrangement here."
        )

    return problems


def assert_production_ready(settings: Settings) -> None:
    """Raise if this configuration must not be deployed."""
    problems = check_production_settings(settings)
    if not problems:
        return

    lines = "\n".join(f"  - {problem}" for problem in problems)
    raise RuntimeError(
        "Refusing to start in production with unsafe configuration.\n"
        f"{lines}\n\n"
        "These defaults are correct for a laptop and wrong for a public URL. "
        "See docs/OPERATIONS.md for the full list of variables."
    )
