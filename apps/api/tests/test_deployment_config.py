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


def _annotation_args(setting: str) -> tuple[str, ...]:
    """The allowed values of a setting typed as a Literal.

    Read off the annotation rather than hardcoded, so the check keeps working if
    the options are ever extended — and fails loudly if the name changes.
    """
    from app.core.config import Settings

    annotation = Settings.model_fields[setting].annotation
    args = getattr(annotation, "__args__", ())
    return tuple(str(a) for a in args)


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


# --------------------------------------------------------------------------- #
# The Render blueprint has now broken a deploy three separate ways, none of     #
# which any test in this file could see.                                       #
#                                                                             #
# 1. `datastore:` where the schema says `databases:`, so the Blueprint could    #
#    not be created at all.                                                    #
# 2. LLM_PROVIDER=none, which is not a member of the Literal that setting is    #
#    typed as. Pydantic raises at import, so the container would exit before    #
#    binding a port.                                                           #
# 3. QUEUE_BACKEND=inline, likewise, and the code it selects could not even be   #
#    constructed: InlineJobQueue imported app.workers.tasks, a module that has  #
#    never existed, so nothing had ever run that path.                          #
#                                                                             #
# All three are invisible to a test that only checks variable *names*. The      #
# checks below load the values.                                                #
# --------------------------------------------------------------------------- #


def blueprint() -> dict:
    import yaml

    return yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))


def test_render_blueprint_only_declares_free_resources() -> None:
    """The committed blueprint must not start charging the reader money.

    A plan id is the one line in a deployment file that costs the operator real
    money without asking. This deployment exists to be run by someone who does
    not have a budget for it, so the paid plans were removed deliberately and
    putting one back should have to be argued for, in a commit message, rather
    than typed into a diff at midnight.
    """
    doc = blueprint()
    paid = [
        f"service {s.get('name')}: {s.get('plan')}"
        for s in doc.get("services", [])
        if s.get("plan") != "free"
    ]
    paid += [
        f"database {d.get('name')}: {d.get('plan')}"
        for d in doc.get("databases", [])
        if d.get("plan") != "free"
    ]
    assert paid == [], f"the free deployment now bills for: {paid}"


def test_render_blueprint_has_no_worker_because_free_render_has_none() -> None:
    """No worker block: free Render does not offer one, so it could not deploy.

    The consequence is handled rather than left as a broken queue —
    QUEUE_BACKEND=inline, which the next test checks is a value that works.
    """
    doc = blueprint()
    workers = [s.get("name") for s in doc.get("services", []) if s.get("type") == "worker"]
    assert workers == [], (
        f"render.yaml declares a background worker ({workers}). Free Render plans "
        "do not support workers, so the Blueprint would fail to create. The work "
        "runs in-process via QUEUE_BACKEND=inline instead."
    )


def test_render_environment_values_are_accepted_by_settings() -> None:
    """Every value in render.yaml must survive Pydantic's validation.

    ``LLM_PROVIDER=none`` read as perfectly reasonable and was not a member of
    the Literal that setting is typed as, so the process raised during import and
    the container exited without ever binding a port. A name that Settings
    recognises can still be given a value Settings rejects, and that is a
    different failure with the same outcome: a deploy that cannot start.

    Checked in a subprocess because settings are built at import time, and the
    repository's own .env would otherwise answer for the blueprint's variables.
    """
    import json
    import os
    import subprocess
    import sys

    doc = blueprint()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DHA_", "HW_"))}
    for var in _literal_bearing_vars():
        env.pop(var, None)
    for service in doc.get("services", []):
        for item in service["envVars"]:
            if "value" in item:
                env[item["key"]] = item["value"]

    # Values a platform supplies rather than the blueprint, filled in so the
    # check reaches the ones it is actually about.
    env["JWT_SECRET"] = "a" * 48
    env["DATABASE_URL"] = "postgresql+psycopg://u:p@host:5432/db"
    env["PUBLIC_BASE_URL"] = "https://archive.onrender.com"
    env["PYTHONPATH"] = str(REPO_ROOT / "apps" / "api")

    probe = (
        "import json;"
        "from app.core.config import Settings;"
        "print(json.dumps(Settings(_env_file=None).model_dump(mode='json')))"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "apps" / "api"),
        check=False,
    )
    assert result.returncode == 0, (
        "render.yaml's environment is rejected by Settings, so the container "
        f"would raise during import and never start:\n{result.stderr[-800:]}"
    )
    resolved = json.loads(result.stdout)
    assert resolved["queue_backend"] == "inline", (
        "the free deployment has no worker, so the queue must run in-process; "
        f"it is configured as {resolved['queue_backend']!r}"
    )
    assert resolved["max_upload_bytes"] <= 64 * 1024 * 1024, (
        "a free web service has 512 MB of memory in total. The default upload "
        f"limit of {resolved['max_upload_bytes']} means one request can exhaust "
        "the instance."
    )


def _literal_bearing_vars() -> list[str]:
    """Names of the settings typed as Literal, upper-cased.

    These are the ones where a plausible-looking value is rejected outright
    rather than ignored, which is the failure this module now guards against.
    """
    from app.core.config import Settings

    out = []
    for name, field in Settings.model_fields.items():
        annotation = str(field.annotation)
        if "Literal" in annotation:
            out.append(name.upper())
    return out


def test_inline_queue_backend_is_selectable() -> None:
    """``QUEUE_BACKEND=inline`` has to resolve, and the code has to be importable.

    The free deployment has no worker, so this is the path every upload takes.
    It was selected by a setting that did not list it, and resolved to a class
    importing a module that has never existed. A setting pointing at a
    constructed-but-unusable class is the worst of both: it looks configured.
    """
    from app.providers.jobs import InlineJobQueue, queue_capability

    assert "inline" in _annotation_args("queue_backend"), (
        "Settings types queue_backend as a Literal that does not include "
        "'inline', so the free deployment cannot select the queue it depends on."
    )

    queue = InlineJobQueue()
    assert queue.name == "inline"
    assert queue.is_available() is True
    # Constructing it is the test: __init__ resolves the module that runs jobs.
    assert callable(queue._run)

    # The suite runs with the default database queue, so the health response for
    # the inline arrangement has to be produced deliberately.
    import app.providers.jobs as jobs

    original = jobs.get_queue
    jobs.get_queue = lambda: queue
    try:
        capability = jobs.queue_capability()
    finally:
        jobs.get_queue = original

    assert capability["backend"] == "inline"
    assert capability["detail"], (
        "a worker count of zero is true and alarming at once. The health response "
        "has to say what it means, or a reader concludes the queue is broken."
    )
