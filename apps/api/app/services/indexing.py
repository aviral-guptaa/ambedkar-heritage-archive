"""Vector and full-text indexing.

Two indexes are maintained for every indexed chunk:

* a ``tsvector`` search vector for PostgreSQL full-text search, and
* a pgvector column for semantic similarity.

Both are rebuilt from scratch on re-index so the index can never drift from the
chunk text. The embedding model name is stored on every row, and a model change
invalidates the vector index rather than silently mixing vector spaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, func, literal_column, or_, select, update


from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.archive import Document, DocumentChunk, DocumentPage
from app.models.enums import IndexStatus, ProcessingState
from app.providers.embedding import get_embedding_provider
from app.services.preservation import slugify

log = get_logger(__name__)

#: Weighting: the title and any heading-ish text outweigh body prose so that
#: "What did he say about the Constitution" ranks the right document.
# Postgres' setweight() takes its weight as the internal "char" type. A bound
# string parameter arrives as varchar, which does not match, so the weights are
# emitted as untyped literals and let Postgres resolve them.
def _tsv_concat(left, right):  # noqa: ANN001, ANN202
    """Concatenate two tsvectors.

    The SQL ``concat()`` keyword is text-only, so tsvector parts must be joined
    with the ``||`` operator.
    """
    return left.op("||")(right)


TSV_WEIGHTS = {
    "title": literal_column("'A'"),
    "section": literal_column("'B'"),
    "body": literal_column("'C'"),
    "page": literal_column("'C'"),
}


def embedding_model_name(provider: object | None = None) -> str:
    """The identifier stored on every chunk for the vector it holds.

    Providers expose this as ``model_id`` or ``model``; only the short ``name``
    (e.g. "hashing") identifies the algorithm, not the tokenisation or
    dimension. Recording ``name`` would make stale vectors undetectable, because
    a version bump would leave the stored value unchanged.
    """
    provider = provider or get_embedding_provider()
    for attribute in ("model", "model_id"):
        value = getattr(provider, attribute, None)
        if value:
            return str(value)
    return str(getattr(provider, "name", "unknown"))


def build_tsv(db: Session, document: Document, chunks: list[DocumentChunk]) -> int:
    """(Re)generate the document and chunk search vectors."""
    db.execute(
        update(Document)
        .where(Document.id == document.id)
        .values(
            search_tsv=func.to_tsvector(
                settings.search_config,
                func.concat(
                    "title ", func.coalesce(document.title, ""), " ",
                    "author ", func.coalesce(document.author_display, ""), " ",
                    "venue ", func.coalesce(document.venue, ""), " ",
                    "location ", func.coalesce(document.location_text, ""), " ",
                    "summary ", func.coalesce(document.summary, ""), " ",
                ),
            )
        )
    )
    for chunk in chunks:
        # setweight requires a literal weight, so the expression is built per row.
        expr = func.setweight(
            func.to_tsvector(settings.search_config, func.coalesce(chunk.text, "")),
            TSV_WEIGHTS["body"],
        )
        if chunk.section:
            expr = _tsv_concat(
                expr,
                func.setweight(
                    func.to_tsvector(settings.search_config, chunk.section),
                    TSV_WEIGHTS["section"],
                ),
            )
        if chunk.page_number:
            expr = _tsv_concat(
                expr,
                func.setweight(
                    func.to_tsvector(settings.search_config, func.cast(chunk.page_number, Text)),
                    TSV_WEIGHTS["page"],
                ),
            )
        db.execute(
            update(DocumentChunk).where(DocumentChunk.id == chunk.id).values(search_tsv=expr)
        )
    db.flush()
    return len(chunks)


def write_chunk_vectors(db: Session, chunks: list[DocumentChunk], *, batch: int = 64) -> int:
    """Embed chunks and store the vectors. Returns the number embedded."""
    if not chunks:
        return 0
    provider = get_embedding_provider()
    model_name = embedding_model_name(provider)
    if provider.dimension != settings.embedding_dim:
        log.warning(
            "embedding dimension mismatch",
            provider_dimension=provider.dimension,
            configured=settings.embedding_dim,
        )
    written = 0
    for start in range(0, len(chunks), batch):
        window = chunks[start : start + batch]
        texts = [c.text for c in window]
        try:
            vectors = provider.embed_documents(texts)
        except Exception as exc:  # noqa: BLE001
            log.error("embedding batch failed", error=str(exc), provider=provider.name)
            return written
        for chunk, vector in zip(window, vectors, strict=False):
            chunk.embedding = vector
            chunk.embedding_model = model_name
            chunk.embedded_at = datetime.now(UTC)
            written += 1
        db.flush()
    return written


def reindex_document(db: Session, document: Document) -> dict[str, Any]:
    """Recompute the full-text vector only (embeddings untouched)."""
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    build_tsv(db, document, list(chunks))
    db.commit()
    return {"document_id": document.id, "chunks": len(chunks), "fts": True}


def embedding_index_status(db: Session) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(DocumentChunk)) or 0
    embedded = (
        db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.embedding.isnot(None))
        )
        or 0
    )
    models = db.execute(
        select(DocumentChunk.embedding_model, func.count())
        .where(DocumentChunk.embedding_model.isnot(None))
        .group_by(DocumentChunk.embedding_model)
    ).all()
    provider = get_embedding_provider()
    return {
        "chunks": int(total),
        "embedded": int(embedded),
        "pending": int(total - embedded),
        "models": {m: int(c) for m, c in models},
        "active_model": embedding_model_name(provider),
        "dimension": provider.dimension,
    }


def stale_vector_count(db: Session) -> int:
    """Chunks whose stored vectors came from a different model than the active one."""
    provider = get_embedding_provider()
    active = embedding_model_name(provider)
    rows = db.execute(
        select(DocumentChunk.embedding_model, func.count())
        .where(DocumentChunk.embedding.isnot(None))
        .group_by(DocumentChunk.embedding_model)
    ).all()
    return int(sum(c for m, c in rows if m != active))


def rebuild_embeddings(db: Session, *, document_id: str | None = None, limit: int = 500) -> int:
    """Re-embed chunks with the currently configured model."""
    provider = get_embedding_provider()
    model_name = embedding_model_name(provider)
    # Chunks with no vector at all are included too: a failed write leaves a
    # NULL embedding that would otherwise never be retried.
    stmt = select(DocumentChunk).where(
        or_(
            DocumentChunk.embedding_model != model_name,
            DocumentChunk.embedding.is_(None),
        )
    )
    if document_id:
        stmt = stmt.where(DocumentChunk.document_id == document_id)
    chunks = db.scalars(stmt.limit(limit)).all()
    if not chunks:
        return 0
    vectors = provider.embed_documents([c.text for c in chunks])
    for chunk, vector in zip(chunks, vectors, strict=False):
        chunk.embedding = vector
        chunk.embedding_model = model_name
        chunk.embedded_at = datetime.now(UTC)
    db.commit()
    return len(chunks)


def unembedded_chunks(db: Session, document: Document, limit: int = 500) -> list[DocumentChunk]:
    return list(
        db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id, DocumentChunk.embedding.is_(None))
            .limit(limit)
        ).all()
    )


def index_queue_summary(db: Session) -> dict[str, Any]:
    counts = dict(
        db.execute(
            select(Document.processing_state, func.count())
            .where(Document.deleted_at.is_(None))
            .group_by(Document.processing_state)
        ).all()
    )
    return {state.value if hasattr(state, "value") else str(state): int(n) for state, n in counts.items()}


@dataclass(slots=True)
class IndexReport:
    document_id: str
    chunks: int
    embedded: int
    status: IndexStatus

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "status": self.status.value,
        }
