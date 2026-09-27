"""Operational records: jobs, integrity checks, RAG audit, stories, i18n."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AnswerKind, JobKind, JobState


def _uuid() -> str:
    return str(uuid.uuid4())


class ProcessingJob(Base):
    """Durable job record. Survives worker restarts; inspectable by admins."""

    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[JobKind] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[JobState] = mapped_column(String(16), default=JobState.QUEUED, nullable=False, index=True)
    backend: Mapped[str] = mapped_column(String(16), default="database", nullable=False)
    stage: Mapped[str | None] = mapped_column(String(48))
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    media_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_jobs_state_kind", "state", "kind"),)


class OcrJob(Base):
    """OCR run record, distinct from the generic job so the UI can page it."""

    __tablename__ = "ocr_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_pages.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    state: Mapped[JobState] = mapped_column(String(16), default=JobState.QUEUED, nullable=False, index=True)
    engine: Mapped[str | None] = mapped_column(String(80))
    engine_version: Mapped[str | None] = mapped_column(String(60))
    language: Mapped[str | None] = mapped_column(String(8))
    preprocessing: Mapped[dict] = mapped_column(JSON, default=dict)
    result_id: Mapped[str | None] = mapped_column(String(36))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    requested_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegrityCheck(Base):
    __tablename__ = "integrity_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(24), default="document", nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    current_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    checked_by: Mapped[str | None] = mapped_column(String(36))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (Index("ix_integrity_doc_status", "document_id", "status"),)


class RagQuery(Base):
    """Full audit trail of every research-assistant answer."""

    __tablename__ = "rag_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    answer_language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    answer_kind: Mapped[AnswerKind] = mapped_column(
        String(32), default=AnswerKind.GROUNDED, nullable=False, index=True
    )
    grounded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    groundedness: Mapped[float | None] = mapped_column(Float)
    retrieval: Mapped[dict] = mapped_column(JSON, default=dict)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    llm_provider: Mapped[str | None] = mapped_column(String(60))
    llm_model: Mapped[str | None] = mapped_column(String(120))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    citation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicting: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    session_key: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class Citation(Base):
    """Persisted citation: answer -> chunk -> document -> page -> source."""

    __tablename__ = "citations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    query_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_queries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_id: Mapped[str | None] = mapped_column(String(36), index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), index=True)
    page_id: Mapped[str | None] = mapped_column(String(36), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    query: Mapped[RagQuery] = relationship()


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(400))
    summary: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    level: Mapped[str] = mapped_column(String(24), default="general", nullable=False)
    cover_media_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media.id", ondelete="SET NULL")
    )
    narration_media_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media.id", ondelete="SET NULL")
    )
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    chapters: Mapped[list["StoryChapter"]] = relationship(
        back_populates="story",
        cascade="all, delete-orphan",
        order_by="StoryChapter.chapter_number",
    )


class StoryChapter(Base):
    __tablename__ = "story_chapters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    story_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_media_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media.id", ondelete="SET NULL")
    )
    audio_media_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media.id", ondelete="SET NULL")
    )
    video_media_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media.id", ondelete="SET NULL")
    )
    event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="SET NULL")
    )
    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL")
    )
    pull_quote: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    source_note: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    story: Mapped[Story] = relationship(back_populates="chapters")
    links: Mapped[list["StoryChapterLink"]] = relationship(
        back_populates="chapter", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (UniqueConstraint("story_id", "chapter_number", name="uq_story_chapter"),)


class StoryChapterLink(Base):
    """Explicit citation / cross-reference for a story chapter."""

    __tablename__ = "story_chapter_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("story_chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(240))
    note: Mapped[str | None] = mapped_column(Text)

    chapter: Mapped[StoryChapter] = relationship(back_populates="links")

    __table_args__ = (
        Index("ix_chapter_link_target", "target_type", "target_id"),
    )


class UiTranslation(Base):
    __tablename__ = "ui_translations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lang: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("lang", "key", name="uq_ui_translation"),)


class EvaluationRun(Base):
    """Persisted RAG / OCR evaluation results. Real measurements only."""

    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset: Mapped[str | None] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    per_item: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
