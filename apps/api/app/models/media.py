"""Audio / video / image archive with searchable transcripts."""

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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import MediaKind


def _uuid() -> str:
    return str(uuid.uuid4())


class Media(Base):
    __tablename__ = "media"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    kind: Mapped[MediaKind] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    media_date: Mapped[date | None] = mapped_column(Date, index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    creator: Mapped[str | None] = mapped_column(String(240))
    speaker: Mapped[str | None] = mapped_column(String(240))
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False, index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    rights: Mapped[str | None] = mapped_column(String(200))
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id", ondelete="SET NULL")
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)
    thumbnail_key: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    publication_status: Mapped[str] = mapped_column(String(20), default="published", nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    transcripts: Mapped[list["MediaTranscript"]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )
    tags: Mapped[list["MediaTag"]] = relationship(back_populates="media", lazy="selectin")
    relations: Mapped[list["MediaRelation"]] = relationship(back_populates="media", lazy="selectin")

    search_tsv = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (
        Index("ix_media_kind_status", "kind", "publication_status"),
        Index("ix_media_search_tsv", "search_tsv", postgresql_using="gin"),
    )


class MediaTranscript(Base):
    __tablename__ = "media_transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    media_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="transcript", nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    start_offset: Mapped[float | None] = mapped_column(Float)
    end_offset: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    confidence: Mapped[float | None] = mapped_column(Float)
    is_searchable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    media: Mapped[Media] = relationship(back_populates="transcripts")
    search_tsv = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (
        UniqueConstraint("media_id", "language", "kind", name="uq_media_transcript"),
        Index("ix_transcript_search_tsv", "search_tsv", postgresql_using="gin"),
    )


class MediaTag(Base):
    __tablename__ = "media_tags"

    media_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(80), primary_key=True, index=True)

    media: Mapped[Media] = relationship(back_populates="tags")


class MediaRelation(Base):
    __tablename__ = "media_relations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    media_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(160))

    media: Mapped[Media] = relationship(back_populates="relations")

    __table_args__ = (
        UniqueConstraint("media_id", "target_type", "target_id", name="uq_media_relation"),
    )
