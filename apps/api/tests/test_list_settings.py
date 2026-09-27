"""List-valued settings must arrive as lists, whatever the platform sends.

These fields carry ``NoDecode`` so pydantic-settings leaves the string alone,
which makes this validator the only place the format is handled. That is worth
testing directly, because the failure mode is quiet: ``CORS_ORIGINS=[]`` split on
commas is the one-element list ``["[]"]``, a literal origin string that no
browser will ever match. Nothing raises, the service starts, and cross-origin
sign-in simply never works.

Deployment platforms differ in what they will let you type into an environment
field, so all three formats have to work.
"""

from __future__ import annotations

import pytest

# NOTE: `app.core.config` is imported inside each test, never at module scope.
# Settings are built at first import, and pytest imports test modules before any
# fixture runs, so a module-level import would freeze them against the
# developer's real `.env` and database. The failure looks unrelated: other tests
# break only when this file is collected. See the note above TEST_ENVIRONMENT in
# conftest.py.


def parse(field: str, raw: str) -> list[str]:
    """Run one setting through the real validator."""
    from app.core.config import Settings

    return getattr(Settings(**{field: raw}), field)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # JSON, which is what people write and what a YAML value looks like.
        ("[]", []),
        ('["https://a.example.org"]', ["https://a.example.org"]),
        (
            '["https://a.example.org", "https://b.example.org"]',
            ["https://a.example.org", "https://b.example.org"],
        ),
        # Comma-separated, for a plain shell export.
        (
            "https://a.example.org,https://b.example.org",
            ["https://a.example.org", "https://b.example.org"],
        ),
        ("https://a.example.org", ["https://a.example.org"]),
        # Empty means empty, not a list containing an empty string.
        ("", []),
        ("   ", []),
        # Whitespace around JSON entries.
        ('[ "https://a.example.org" ]', ["https://a.example.org"]),
    ],
)
def test_cors_origins_formats(raw: str, expected: list[str]) -> None:
    assert parse("cors_origins", raw) == expected


@pytest.mark.parametrize("malformed", ["[not json", '["https://a.example.org", ]'])
def test_malformed_values_degrade_to_a_visible_list(malformed: str) -> None:
    """A typo should not crash the service.

    Falling back to the comma split leaves a value that is visibly wrong, rather
    than raising at import time and taking the whole process down over an
    environment variable. ``["https://a.example.org", ]`` is not valid JSON — a
    trailing comma — so it is expected here to be split on commas.
    """
    parsed = parse("cors_origins", malformed)
    assert parsed and all(isinstance(item, str) for item in parsed)


def test_a_list_passes_through_untouched() -> None:
    """The common case: a real list from a YAML file or a test fixture."""
    from app.core.config import Settings

    value = ["https://a.example.org"]
    assert Settings(cors_origins=value).cors_origins == value


def test_other_list_settings_accept_the_same_formats() -> None:
    """The validator is shared, so check it on a second field."""
    assert parse("ocr_languages", "eng,hin,mar") == ["eng", "hin", "mar"]
    assert parse("ocr_languages", '["eng", "hin"]') == ["eng", "hin"]
