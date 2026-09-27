"""Knowledge layer: entities, events, articles and the relationship graph."""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.archive import Document
from app.models.enums import EntityType, RelationType


def _uuid() -> str:
    return str(uuid.uuid4())


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    alternate_names: Mapped[list] = mapped_column(JSON, default=list)
    birth_year: Mapped[int | None] = mapped_column(Integer)
    death_year: Mapped[int | None] = mapped_column(Integer)
    bio: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    documents: Mapped[list["DocumentPerson"]] = relationship(back_populates="person")


class Place(Base):
    __tablename__ = "places"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    region: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(140), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    kind: Mapped[str] = mapped_column(String(60), default="institution")
    description: Mapped[str | None] = mapped_column(Text)


class Committee(Base):
    __tablename__ = "committees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(140), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    formed_year: Mapped[int | None] = mapped_column(Integer)
    dissolved_year: Mapped[int | None] = mapped_column(Integer)
    chair_person_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("persons.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(60), default="theme", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    synonyms: Mapped[list] = mapped_column(JSON, default=list)

    documents: Mapped[list["TopicLink"]] = relationship(back_populates="topic")


class ConstitutionalArticle(Base):
    __tablename__ = "constitutional_articles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    article_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    part: Mapped[str | None] = mapped_column(String(80))
    chapter: Mapped[str | None] = mapped_column(String(160))
    heading: Mapped[str | None] = mapped_column(String(240))
    summary: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    amended_by: Mapped[str | None] = mapped_column(String(300))
    effective_from: Mapped[str | None] = mapped_column(String(80))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_article_part_chapter", "part", "chapter"),)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[date | None] = mapped_column(Date, index=True)
    date_label: Mapped[str | None] = mapped_column(String(80))
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    date_precision: Mapped[str] = mapped_column(String(24), default="day", nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(60), index=True)
    place_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("places.id", ondelete="SET NULL")
    )
    place_label: Mapped[str | None] = mapped_column(String(200))
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id", ondelete="SET NULL")
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    significance: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    documents: Mapped[list["EventDocument"]] = relationship(back_populates="event")
    people: Mapped[list["EventPerson"]] = relationship(back_populates="event")

    __table_args__ = (Index("ix_events_year_type", "year", "event_type"),)


class EventDocument(Base):
    __tablename__ = "event_documents"

    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(40), default="documented_by", nullable=False)

    event: Mapped[Event] = relationship(back_populates="documents")
    document: Mapped[Document] = relationship(back_populates="events")


class EventPerson(Base):
    __tablename__ = "event_people"

    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(60), default="participant", nullable=False)

    event: Mapped[Event] = relationship(back_populates="people")


class DocumentPerson(Base):
    __tablename__ = "document_people"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(60), default="author", nullable=False)

    document: Mapped[Document] = relationship(back_populates="people")
    person: Mapped[Person] = relationship(back_populates="documents")


class TopicLink(Base):
    __tablename__ = "topic_documents"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="curated", nullable=False)

    document: Mapped[Document] = relationship(back_populates="topics")
    topic: Mapped[Topic] = relationship(back_populates="documents")


class Relationship(Base):
    """Curated / extracted edge awaiting or holding archivist approval.

    Low-confidence extracted edges are stored with ``status='pending'`` and are
    never served to the public graph until reviewed.
    """

    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    from_type: Mapped[EntityType] = mapped_column(String(40), nullable=False, index=True)
    from_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    to_type: Mapped[EntityType] = mapped_column(String(40), nullable=False, index=True)
    to_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relation: Mapped[RelationType] = mapped_column(String(40), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="approved", nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(40), default="curated", nullable=False)
    evidence_chunk_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    evidence_text: Mapped[str | None] = mapped_column(Text)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    reviewed_by: Mapped[str | None] = mapped_column(String(36))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("from_type", "from_id", "to_type", "to_id", "relation", name="uq_rel_edge"),
        Index("ix_rel_from", "from_type", "from_id", "status"),
        Index("ix_rel_to", "to_type", "to_id", "status"),
    )


class GraphNode(Base):
    """Canonical entity mirror. Also the fallback graph store when Neo4j is off."""

    __tablename__ = "graph_nodes"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    label: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    entity_type: Mapped[EntityType] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    degree: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_graph_nodes_type_name", "entity_type", "name"),
    )


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    src_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dst_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[RelationType] = mapped_column(String(40), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    relationship_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("relationships.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    src: Mapped[GraphNode] = relationship(foreign_keys=[src_id])
    dst: Mapped[GraphNode] = relationship(foreign_keys=[dst_id])

    __table_args__ = (
        Index("ix_graph_edge_src_rel", "src_id", "relation"),
        Index("ix_graph_edge_dst_rel", "dst_id", "relation"),
    )
