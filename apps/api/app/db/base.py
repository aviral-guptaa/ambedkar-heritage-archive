"""SQLAlchemy declarative base, engine and session management."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from pgvector.sqlalchemy import Vector
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Explicit naming convention so Alembic autogenerate produces stable names and
# ON DELETE constraints can reference a constraint.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def as_dict(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def _build_engine():
    kwargs: dict = {
        "echo": settings.database_echo,
        "pool_pre_ping": True,
        "future": True,
    }
    if not settings.database_url.startswith("sqlite"):
        kwargs.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
        )
    else:  # pragma: no cover - sqlite is only used by the unit-test harness
        from sqlalchemy.pool import StaticPool

        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    return create_engine(settings.database_url, **kwargs)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

#: Embedding vector column, sized from configuration. Kept in one place so
#: models, migrations and the search layer cannot drift apart.
EMBEDDING_DIM = settings.embedding_dim
EmbeddingVector = Vector(EMBEDDING_DIM)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background jobs, scripts and workers."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def register_vector(connection) -> bool:  # pragma: no cover - driver glue
    """Register the pgvector adapter on a raw DBAPI connection."""
    try:
        from pgvector.psycopg import register_vector as _register

        _register(connection)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("pgvector registration skipped", error=str(exc))
        return False
