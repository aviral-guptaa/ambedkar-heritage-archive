"""A production deployment must refuse unsafe configuration.

The defaults in this project are right for a laptop and wrong for a public URL.
The interesting part is that nothing fails loudly on its own: a service with the
development signing secret comes up healthy, answers requests, and only gives
itself away to someone who tries to mint an archivist session.

So the checks are made explicit and tested here, including the case that matters
most — that they are silent in development, where a refusal would be a nuisance
rather than a protection.
"""

from __future__ import annotations

import pytest

# NOTE: `app.*` is imported inside the tests, never at module scope.
#
# Settings are built when `app.core.config` is first imported, and pytest imports
# every test module before it runs a single fixture. A module-level import here
# would freeze the settings object against the developer's own `.env` and their
# real database, before the harness had chosen a throwaway one. The symptom is
# baffling rather than obvious: unrelated tests fail only when this file is
# collected, and each one passes in isolation. See the note above
# TEST_ENVIRONMENT in conftest.py.

#: Spelled out rather than imported, because a `@pytest.mark.parametrize`
#: decorator is evaluated at import time. `test_literal_matches_the_real_default`
#: keeps this honest.
SHIPPED_JWT_SECRET = "dev-only-insecure-secret-change-me"

#: A configuration that should pass, so each test can break exactly one thing.
SAFE = {
    "environment": "production",
    "debug": False,
    "demo_mode": False,
    "jwt_secret": "k" * 48,
    "database_url": "postgresql+psycopg://archive:pw@host:5432/archive",
    "cors_origins": [],
}


def check(**overrides: object) -> list[str]:
    """The problems with a configuration, using the real settings class."""
    from app.core.config import Settings
    from app.core.production_guard import check_production_settings

    return check_production_settings(Settings(**{**SAFE, **overrides}))


def test_literal_matches_the_real_default() -> None:
    """The constant above is a copy; if the original moves, this must fail."""
    from app.core.production_guard import INSECURE_JWT_SECRET

    assert INSECURE_JWT_SECRET == SHIPPED_JWT_SECRET


def test_a_correct_configuration_passes() -> None:
    assert check() == []


def test_development_is_never_blocked() -> None:
    """The guard must not get in the way of running on a laptop.

    Every one of these settings is unsafe in public and normal in development, so
    a guard that fired here would just be something to disable.
    """
    from app.core.config import Settings
    from app.core.production_guard import check_production_settings

    assert check_production_settings(
        Settings(environment="development", debug=True, demo_mode=True)
    ) == []
    assert check_production_settings(Settings(environment="testing")) == []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("jwt_secret", SHIPPED_JWT_SECRET, "JWT_SECRET"),
        ("jwt_secret", "", "JWT_SECRET"),
        ("jwt_secret", "secret", "JWT_SECRET"),
        ("jwt_secret", "short", "JWT_SECRET"),
        ("jwt_secret", "k" * 31, "32 characters"),
        ("debug", True, "DEBUG"),
        ("demo_mode", True, "DEMO_MODE"),
        ("database_url", "sqlite:///./archive.db", "SQLite"),
        ("cors_origins", ["*"], "CORS_ORIGINS"),
    ],
)
def test_unsafe_settings_are_refused(field: str, value: object, expected: str) -> None:
    problems = check(**{field: value})
    assert problems, f"{field}={value!r} was accepted in production"
    assert any(expected in problem for problem in problems), (
        f"the message for {field} does not mention {expected!r}, so it would not "
        f"tell an operator what to change: {problems}"
    )


def test_a_secret_of_exactly_32_characters_is_allowed() -> None:
    """The boundary should be inclusive, and documented as such."""
    assert check(jwt_secret="k" * 32) == []


def test_empty_cors_is_allowed() -> None:
    """Same-origin needs no CORS, and that is the normal arrangement.

    nginx proxies /api for the kiosk and WEB_DIST serves the interface for a
    single-port deployment, so a browser never makes a cross-origin request. An
    earlier version of this guard required a value here, which would have
    blocked the one-port deployment it was written to protect.
    """
    assert check(cors_origins=[]) == []


def test_assert_raises_with_an_actionable_message() -> None:
    from app.core.config import Settings
    from app.core.production_guard import assert_production_ready

    with pytest.raises(RuntimeError) as caught:
        assert_production_ready(Settings(**{**SAFE, "debug": True}))
    assert "DEBUG" in str(caught.value)


def test_every_problem_is_reported_not_just_the_first() -> None:
    """An operator should be able to fix them in one pass, not one per deploy."""
    from app.core.config import Settings
    from app.core.production_guard import assert_production_ready

    with pytest.raises(RuntimeError) as caught:
        assert_production_ready(
            Settings(**{**SAFE, "debug": True, "demo_mode": True, "jwt_secret": "x"})
        )
    message = str(caught.value)
    assert "DEBUG" in message
    assert "DEMO_MODE" in message
    assert "JWT_SECRET" in message


def test_assert_is_silent_when_safe() -> None:
    from app.core.config import Settings
    from app.core.production_guard import assert_production_ready

    assert_production_ready(Settings(**SAFE))
