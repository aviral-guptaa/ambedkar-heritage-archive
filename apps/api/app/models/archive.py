"""Archival core: sources, collections, documents, versions, pages, chunks."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    true,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EmbeddingVector
from app.models.enums import (
    VerificationStatus,
    IndexStatus,
    OcrStatus,
    ProcessingState,
    PublicationStatus,
    SourceTier,
)


def _uuid() -> str:
    return str(uuid.uuid4())


class Source(Base):
    """Provenance record: where a document came from and how it may be used."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(80))
    source_domain: Mapped[str | None] = mapped_column(String(160), index=True)
    publisher: Mapped[str | None] = mapped_column(String(240))
    tier: Mapped[SourceTier] = mapped_column(
        String(32), default=SourceTier.UNKNOWN, nullable=False, index=True
    )
    rights_status: Mapped[str | None] = mapped_column(String(80))
    license: Mapped[str | None] = mapped_column(String(160))
    retrieval_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    documents: Mapped[list["Document"]] = relationship(back_populates="source")

    __table_args__ = (Index("ix_sources_domain_tier", "source_domain", "tier"),)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40), default="theme", nullable=False)
    cover_object_key: Mapped[str | None] = mapped_column(Text)
    accent: Mapped[str | None] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    documents: Mapped[list["Document"]] = relationship(back_populates="collection")

    __table_args__ = (Index("ix_collections_published_sort", "is_published", "sort_order"),)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    author_display: Mapped[str | None] = mapped_column(String(300))
    speaker: Mapped[str | None] = mapped_column(String(300))
    document_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False, index=True)
    original_language: Mapped[str | None] = mapped_column(String(8))

    document_date: Mapped[date | None] = mapped_column(Date, index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    year_from: Mapped[int | None] = mapped_column(Integer)
    year_to: Mapped[int | None] = mapped_column(Integer)
    date_precision: Mapped[str | None] = mapped_column(String(24))
    location_text: Mapped[str | None] = mapped_column(String(300))
    venue: Mapped[str | None] = mapped_column(String(300))
    event_type: Mapped[str | None] = mapped_column(String(80))

    collection_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("collections.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    source_reference: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    rights: Mapped[str | None] = mapped_column(String(200))
    provenance: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[str | None] = mapped_column(String(60))
    editorial_note: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        String(32), default=VerificationStatus.UNVERIFIED_SECONDARY, nullable=False, index=True
    )

    # preservation ------------------------------------------------------
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    storage_key: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # processing state --------------------------------------------------
    processing_state: Mapped[ProcessingState] = mapped_column(
        String(24), default=ProcessingState.UPLOADED, nullable=False, index=True
    )
    ocr_status: Mapped[OcrStatus] = mapped_column(
        String(24), default=OcrStatus.PENDING, nullable=False, index=True
    )
    embedding_status: Mapped[IndexStatus] = mapped_column(
        String(16), default=IndexStatus.PENDING, nullable=False, index=True
    )
    graph_status: Mapped[IndexStatus] = mapped_column(
        String(16), default=IndexStatus.PENDING, nullable=False
    )
    publication_status: Mapped[PublicationStatus] = mapped_column(
        String(20), default=PublicationStatus.DRAFT, nullable=False, index=True
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    collection: Mapped[Collection | None] = relationship(back_populates="documents")
    source: Mapped[Source | None] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentPage.page_number",
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    topics: Mapped[list["TopicLink"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )
    people: Mapped[list["DocumentPerson"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[list["EventDocument"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_documents_published_year", "publication_status", "year"),
        Index("ix_documents_type_lang", "document_type", "language"),
        Index("ix_documents_collection_type", "collection_id", "document_type"),
        Index("ix_documents_search_tsv", "search_tsv", postgresql_using="gin"),
        # Fuzzy title matching for the explore search box.
        Index(
            "ix_documents_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    search_tsv = mapped_column(TSVECTOR, nullable=True)


class DocumentVersion(Base):
    """Immutable archival version (OAIS AIP). Originals are never overwritten."""

    __tablename__ = "document_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_version"),
        Index("ix_docver_doc_current", "document_id", "is_current"),
    )


class DocumentPage(Base):
    """A single page/folio of a document plus its OCR artefacts."""

    __tablename__ = "document_pages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    rotation_degrees: Mapped[float | None] = mapped_column(Float)
    # Original scan is never modified. ``processed_key`` is a derived artefact.
    original_key: Mapped[str | None] = mapped_column(Text)
    processed_key: Mapped[str | None] = mapped_column(Text)
    thumbnail_key: Mapped[str | None] = mapped_column(Text)
    original_sha256: Mapped[str | None] = mapped_column(String(64))
    processed_sha256: Mapped[str | None] = mapped_column(String(64))

    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_corrected_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_language: Mapped[str | None] = mapped_column(String(8))
    ocr_engine: Mapped[str | None] = mapped_column(String(80))
    ocr_duration_ms: Mapped[int | None] = mapped_column(Integer)
    layout: Mapped[dict] = mapped_column(JSON, default=dict)
    detected_entities: Mapped[list] = mapped_column(JSON, default=list)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(36))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="pages")
    ocr_results: Mapped[list["OcrResult"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="OcrResult.created_at.desc()"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_page"),
        Index("ix_pages_doc_number", "document_id", "page_number"),
    )

    @property
    def effective_text(self) -> str:
        """Human-corrected text wins over raw OCR output, never the reverse."""
        return self.ocr_corrected_text or self.ocr_text or ""


class DocumentMetadata(Base):
    """Arbitrary archival metadata key/value pairs per document."""

    __tablename__ = "document_metadata"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(24), default="string", nullable=False)
    field_group: Mapped[str] = mapped_column(String(40), default="descriptive", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("document_id", "key", name="uq_document_meta"),)


class DocumentChunk(Base):
    """A retrieval unit. Carries the page + source pointer for citations."""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_pages.id", ondelete="SET NULL"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str | None] = mapped_column(String(300))
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False, index=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer, index=True)
    # Explicit copy of the citation pointer so it survives document edits.
    source_url: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(String(300))
    volume: Mapped[str | None] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(24), default="text", nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    is_corrected_text: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quote_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        server_default=true(),
        comment="False when the text is a summary or retelling rather than checked source text.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
    page: Mapped[DocumentPage | None] = relationship()
    embedding_row: Mapped["Embedding | None"] = relationship(
        back_populates="chunk", cascade="all, delete-orphan", uselist=False
    )

    search_tsv = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
        Index("ix_chunks_search_tsv", "search_tsv", postgresql_using="gin"),
        Index("ix_chunks_doc_lang", "document_id", "language"),
        Index("ix_chunks_page", "page_id"),
        # ANN index for cosine similarity. Half of the default m/ef settings
        # keeps the 8 GB kiosk build inside memory while staying accurate.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    chunk_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("document_chunks.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunk: Mapped[DocumentChunk] = relationship(back_populates="embedding_row")


class Translation(Base):
    """Translation is always stored *beside* the original, never over it."""

    __tablename__ = "translations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_pages.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True
    )
    source_language: Mapped[str] = mapped_column(String(8), nullable=False)
    target_language: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("chunk_id", "target_language", name="uq_chunk_translation"),
        Index("ix_translation_target", "target_language", "source_language"),
    )


class OcrResult(Base):
    """Versioned OCR output; corrections create a new row, never overwrite."""

    __tablename__ = "ocr_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_pages.id", ondelete="CASCADE"), index=True
    )
    engine: Mapped[str] = mapped_column(String(80), nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String(60))
    language: Mapped[str | None] = mapped_column(String(8))
    raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    corrected_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    blocks: Mapped[list] = mapped_column(JSON, default=list)
    layout: Mapped[dict] = mapped_column(JSON, default=dict)
    preprocessing: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_human_corrected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    page: Mapped[DocumentPage | None] = relationship(back_populates="ocr_results")

    __table_args__ = (Index("ix_ocrresult_doc_current", "document_id", "is_current"),)
