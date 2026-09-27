"""Test harness for the Digital Heritage Archive API.

The archive is built on PostgreSQL features — ``tsvector`` full-text search,
``pgvector`` embeddings and ``pg_trgm`` — so the suite runs against a real
PostgreSQL database rather than SQLite. SQLite cannot render the FTS and vector
types at all, and a suite that passed there would prove nothing about the
behaviour that matters.

Each session gets a database of its own, created and dropped around the run, so
the developer's real archive is never touched and tests never see each other's
rows. The schema is built by running the Alembic migrations rather than
``create_all``, so a broken migration fails the suite instead of being hidden by
a hand-built schema.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

API_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = API_DIR.parents[1]

#: Connection details for the PostgreSQL instance the suite runs against.
#:
#: Locally that is Homebrew's server over a unix socket, as the OS user, who is a
#: superuser and so may create the throwaway databases this suite needs. In CI
#: there is no socket and there is a password, so ``DHA_TEST_PG_DSN`` carries the
#: whole connection string. Both are libpq conninfo strings, which is what makes
#: one code path serve both.
PG_HOST = os.environ.get("DHA_TEST_PG_HOST", "/tmp")
PG_DSN_BASE = os.environ.get("DHA_TEST_PG_DSN", f"host={PG_HOST} dbname=postgres")


def _database_dsn(name: str) -> str:
    """The same server, pointed at a different database."""
    params = conninfo_to_dict(PG_DSN_BASE)
    params["dbname"] = name
    return make_conninfo(**params)


def _sqlalchemy_url(name: str) -> str:
    """A SQLAlchemy URL for a throwaway database on the configured server.

    Handles both shapes: a unix socket has no port and is passed as a query
    parameter, while a TCP host has no password locally but always does in CI.
    """
    params = conninfo_to_dict(_database_dsn(name))
    # libpq leaves `user` unset when it should default to the OS user, and
    # SQLAlchemy cannot infer it, so name it explicitly.
    user = quote_plus(params.get("user") or os.environ.get("USER") or "postgres")
    password = params.get("password")
    host = params.get("host", "")
    port = params.get("port")
    credentials = f"{user}:{quote_plus(password)}" if password else user

    if host.startswith("/"):
        # The socket path goes in raw. Percent-encoding it as %2Ftmp looks
        # correct and is not: SQLAlchemy reads a `%` in a URL as the start of an
        # interpolation and fails with "invalid interpolation syntax".
        return f"postgresql+psycopg://{credentials}@/{name}?host={host}"
    netloc = f"{credentials}@{host}" + (f":{port}" if port else "")
    return f"postgresql+psycopg://{netloc}/{name}"


#: The test environment, applied at import time rather than in a fixture.
#:
#: Settings are built when ``app.core.config`` is first imported, and pytest
#: imports this file before it imports any test module. Setting the environment
#: here is therefore the only point that reliably precedes the application being
#: imported by anything.
#:
#: It used to happen inside the ``database_name`` fixture, which is late: a test
#: module with ``from app.core.config import Settings`` at the top would freeze
#: the settings object from the developer's own ``.env`` at collection time, and
#: the suite would then run against real provider settings, real thresholds and
#: the real database. Nothing failed loudly. Three tests failed, all of them
#: passing in isolation, and the cause was a test file's import line.
TEST_ENVIRONMENT = {
    "ENVIRONMENT": "testing",
    "JWT_SECRET": "test-secret-not-used-for-anything-real",
    "LLM_PROVIDER": "extractive",
    "EMBEDDING_PROVIDER": "hashing",
    "EMBEDDING_MODEL": "dha-hashing-v2",
    "RERANKER_PROVIDER": "lexical",
    "QUEUE_BACKEND": "database",
    "STORAGE_BACKEND": "filesystem",
    "GRAPH_BACKEND": "postgres",
    "MINIO_ENDPOINT": "",  # keep the object store off the network
    "NEO4J_URI": "",  # force the relational graph mirror
    "OCR_PROVIDER": "tesseract",
    # Keep the archive's own files out of the developer's tree.
    "LOCAL_STORAGE_PATH": str(API_DIR / "var" / "test-storage"),
    "STORAGE_ROOT": str(REPO_DIR / "apps" / "api" / "var" / "test-runtime"),
}

for _key, _value in TEST_ENVIRONMENT.items():
    os.environ[_key] = _value


@pytest.fixture(scope="session")
def database_name() -> Iterator[str]:
    """Create a throwaway database for the session and drop it afterwards."""
    name = f"dha_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(PG_DSN_BASE, autocommit=True)
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()

    # Extensions are not copied into a fresh database on every major version, so
    # create them explicitly. The migrations expect both to exist.
    conn = psycopg.connect(_database_dsn(name), autocommit=True)
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    finally:
        conn.close()

    # Only DATABASE_URL belongs here. It depends on the generated name, so it
    # cannot be known until the database exists. Everything else was set at
    # module scope, before any test module was imported.
    os.environ["DATABASE_URL"] = _sqlalchemy_url(name)

    try:
        yield name
    finally:
        admin = psycopg.connect(PG_DSN_BASE, autocommit=True)
        try:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            admin.close()


@pytest.fixture(scope="session")
def migrated_database(database_name: str) -> Iterator[str]:
    """Bring the throwaway database to the current Alembic head."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")
    yield database_name


@pytest.fixture(scope="session")
def assert_isolated_database(app_module):
    """Fail loudly if the suite is pointed at a real archive.

    A test module that imports ``app.*`` at module scope freezes the settings
    object before this harness has chosen a throwaway database, so the suite
    quietly runs against the developer's own archive instead. The visible
    symptoms are terrible: the seeded fixtures write rows into a real database,
    and a handful of unrelated tests fail only when that module happens to be
    collected — each one passing in isolation, which sends you looking in
    entirely the wrong place.

    The invariant is "no test module imports the application at module scope",
    which is not something a test can enforce about another file. This at least
    refuses to let it pass unnoticed, and says what to do.
    """
    from app.core.config import settings

    name = urlparse(settings.database_url).path.lstrip("/")
    if not name.startswith("dha_test_"):
        raise RuntimeError(
            f"The suite is connected to {name!r}, not a throwaway test database.\n"
            "Something imported the application before tests/conftest.py chose a "
            "database — almost always a module-level `from app... import ...` in a "
            "test file. pytest imports test modules before any fixture runs, so "
            "the settings object is built from the developer's .env and never "
            "updated.\n"
            "Move the import inside the test function."
        )
    return app_module


@pytest.fixture(scope="session")
def app_module(migrated_database: str):
    """Import the application once the database and settings are ready."""
    import sys

    sys.path.insert(0, str(API_DIR))
    from app.main import create_app

    return create_app()


@pytest.fixture(scope="session")
def client(assert_isolated_database):
    """The application under test, behind a client.

    Takes the isolation check rather than ``app_module`` directly so the
    assertion runs before anything can open a connection to a real archive.
    The check returns the application, so this is the same object.
    """
    from fastapi.testclient import TestClient

    with TestClient(assert_isolated_database) as test_client:
        yield test_client


@pytest.fixture()
def db(client):
    """A session bound to the same database the app uses, rolled back after."""
    from app.db.base import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="session")
def session_factory(client):
    from app.db.base import SessionLocal

    return SessionLocal


@pytest.fixture(scope="session")
def seeded(client, session_factory):
    """A small archive for the honesty tests to assert against.

    Built the way the corpus importer builds records — a document, its stored
    text version, and page 0 for "no pagination" — so the tests exercise the
    same paths the real import did rather than a convenient shortcut.
    """
    from app.db.base import session_scope
    from app.models.archive import Document
    from app.services.ingestion import index_document

    if session_factory().query(Document).filter(Document.slug == "caste-in-india-1916").first():
        return True

    with session_scope() as db:
        source = _source(db)
        _record(
            db,
            source,
            index_document,
            slug="caste-in-india-1916",
            title="Castes in India: Their Mechanism, Genesis and Development",
            text=(
                "It is a truism that in India the caste system has operated as a mechanism "
                "for the subordination of groups. The Brahmans have always claimed their "
                "superiority by the purity of their blood. Endogamy and exogamy are the "
                "rules by which caste is maintained and by which it is reproduced. The "
                "abolition of untouchability requires that the mind of man be made free "
                "from the shackles of hereditary pollution. Untouchability is therefore a "
                "sin and the spirit of the Constitution demands its abolition without "
                "delay in every part of the country."
            ),
            date_value="1916-11-16",
            precision="day",
            year=1916,
            summary="A paper on the origin and mechanism of the caste system.",
        )
        _record(
            db,
            source,
            index_document,
            slug="women-and-education-1948",
            title="Women and Education",
            text=(
                "The educational disadvantage of women in our country is a matter of grave "
                "concern. Until the marriage market is broken, education for women will "
                "remain a privilege of a few families. The mother is the first teacher of "
                "the child and the education of the mother is the education of a nation."
            ),
            date_value=None,
            precision="year",
            year=1948,
            summary=None,
        )
    return True


def _source(db):
    from app.models.archive import Source
    from app.models.enums import SourceTier

    existing = db.query(Source).filter(Source.name == "Test primary source").one_or_none()
    if existing:
        return existing
    source = Source(
        name="Test primary source",
        source_url="https://example.org/speeches",
        source_type="book",
        publisher="Test repository",
        tier=SourceTier.GOVERNMENT_PRIMARY.value,
        rights_status="public_domain",
        notes="Fixture source for the test suite.",
    )
    db.add(source)
    db.flush()
    return source


def _record(db, source, index_document, *, slug, title, text, date_value, precision, year, summary):
    """Create one unverified record and index it, as the importer does."""
    from app.models.archive import Document
    from app.models.enums import (
        DocumentType,
        ProcessingState,
        PublicationStatus,
        VerificationStatus,
    )

    document = Document(
        slug=slug,
        title=title,
        summary=summary,
        speaker="Dr. B. R. Ambedkar",
        author_display="Dr. B. R. Ambedkar",
        document_type=DocumentType.SPEECH.value,
        language="en",
        original_language="en",
        document_date=date_value,
        year=year,
        date_precision=precision,
        source_id=source.id,
        source_url=source.source_url,
        source_reference=f"TEST-{slug}",
        external_id=f"TEST-{slug}",
        rights="public_domain",
        provenance="Imported by the test suite from a fixed fixture.",
        verification_status=VerificationStatus.UNVERIFIED_SECONDARY,
        checksum_sha256="0" * 64,
        mime_type="text/markdown",
        processing_state=ProcessingState.READY,
        publication_status=PublicationStatus.PUBLISHED,
    )
    db.add(document)
    db.flush()
    # Page 0 is the archive's marker for "no pagination", so a citation can never
    # invent a page number for continuous text.
    index_document(db, document, pages=[(0, text)])
    return document
