"""The deployment files must name variables the application actually reads.

This is here because it was already got wrong once.

The application's settings have no environment prefix (only the hardware profile
uses ``HW_``), so a variable called ``DHA_DATABASE_URL`` is not an unusual
spelling of the real one — it is a variable the process never reads. Every
Dockerfile and Compose file in this repository was written with that prefix, so
a container built from them would have come up in development mode, with the
shipped signing secret, pointed at a database on localhost, and looked healthy
while doing it.

Nothing in that failure is visible from the outside. So the names are checked
here instead, against the settings themselves, and a name the application does
not recognise fails the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Names that are not application settings but legitimately appear in a
#: deployment file. Each is here because it is genuinely not a setting.
NOT_A_SETTING = {
    # Read by the container's CMD, or standard for the base image.
    "PORT",
    "PATH",
    "NODE_ENV",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "PIP_NO_CACHE_DIR",
    "PIP_DISABLE_PIP_VERSION_CHECK",
    # Postgres container plumbing, not application configuration.
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_INITDB_ARGS",
    # Ports published by Compose, not application configuration.
    "API_PORT",
    "WEB_PORT",
    # A psql flag on a CREATE EXTENSION command.
    "ON_ERROR_STOP",
}


def recognised_names() -> set[str]:
    """Every environment variable name the application will act on.

    Imported here rather than at module scope on purpose. Settings are built at
    import time from the environment, and ``tests/conftest.py`` only sets the
    test environment inside a session fixture — which runs *after* collection.
    Importing the settings at module level would freeze the developer's own
    ``.env`` into the suite before the harness had a chance to say otherwise.
    """
    from app.core.config import HardwareConfig, Settings

    plain = {name.upper() for name in Settings.model_fields}
    hardware = {f"HW_{name}".upper() for name in HardwareConfig.model_fields}
    return plain | hardware


def names_in(path: Path) -> set[str]:
    """Variable names a deployment file assigns."""
    text = path.read_text(encoding="utf-8")
    found: set[str] = set()
    if path.suffix in {".yaml", ".yml"}:
        # Render blueprints declare variables as `- key: NAME` entries.
        found |= set(re.findall(r"^\s*-\s*key:\s*([A-Z][A-Z0-9_]*)\s*$", text, re.M))
        return found
    # `KEY=value` and `ENV KEY=value`, including the backslash continuations used
    # for long environment blocks.
    found |= set(re.findall(r"^\s*(?:ENV\s+)?([A-Z][A-Z0-9_]*)\s*=", text, re.M))
    found |= set(re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*:", text, re.M))
    found |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)[:}]", text))
    return found


DEPLOYMENT_FILES = [
    "Dockerfile",
    "Dockerfile.kiosk",
    "Dockerfile.render",
    "docker-compose.yml",
    ".env.example",
    "render.yaml",
]


@pytest.mark.parametrize("filename", DEPLOYMENT_FILES)
def test_deployment_files_only_use_known_variables(filename: str) -> None:
    path = REPO_ROOT / filename
    if not path.is_file():
        pytest.skip(f"{filename} is not present")

    unknown = sorted(names_in(path) - recognised_names() - NOT_A_SETTING)
    assert unknown == [], (
        f"{filename} sets {unknown}, which the application does not read.\n"
        "Settings have no environment prefix, so a DHA_-prefixed name is not a "
        "variant of the real one — it is silently ignored, and the service would "
        "start in development mode with the default signing secret."
    )


def test_settings_actually_have_no_environment_prefix() -> None:
    """Guards the assumption the rest of this file rests on.

    If a prefix were ever added to Settings, every deployment file would need
    rewriting and this test would be checking the wrong thing. Failing here says
    which, rather than leaving a stale-but-passing check behind.
    """
    from app.core.config import HardwareConfig, Settings

    assert not Settings.model_config.get("env_prefix"), (
        "Settings has gained an env_prefix. Every variable in the Dockerfiles, "
        "docker-compose.yml and .env.example now needs that prefix added, and "
        "the tests in this module are no longer meaningful."
    )
    assert HardwareConfig.model_config.get("env_prefix") == "HW_"


def test_env_example_does_not_ship_a_secret() -> None:
    """The example must be safe to publish."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"^JWT_SECRET=\S", text, re.M), (
        "JWT_SECRET must not be given a value in .env.example. The production "
        "guard rejects the shipped default, so an example value would imply a "
        "problem that had already been solved."
    )
