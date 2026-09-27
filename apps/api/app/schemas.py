"""Pydantic request/response contracts.

These schemas are the single source of truth for the TypeScript client. The
frontend types in `packages/types` are generated from the OpenAPI document, so a
contract change on either side is a visible diff rather than a silent mismatch.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Alias used where a model has a field literally named ``date``; a bare
#: ``date`` annotation would resolve to the field, not the type.
DateField = date


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# system
# --------------------------------------------------------------------------- #


class ComponentStatus(BaseModel):
    name: str
    status: Literal["ok", "degraded", "unavailable"]
    detail: str
    required_for: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    environment: str
    database: ComponentStatus
    storage: ComponentStatus
    queue: ComponentStatus
    graph: ComponentStatus
    ocr: ComponentStatus
    embedding: ComponentStatus
    llm: ComponentStatus
    speech: ComponentStatus
    translation: ComponentStatus
    components: list[ComponentStatus] = Field(default_factory=list)
    checked_at: datetime


class CapabilitiesResponse(BaseModel):
    """Everything the UI needs to enable, disable or honestly label a feature."""

    storage_backend: str
    graph_backend: str
    queue_backend: str
    ocr: dict[str, Any]
    embedding: dict[str, Any]
    reranker: dict[str, Any]
    llm: dict[str, Any]
    stt: dict[str, Any]
    tts: dict[str, Any]
    translation: dict[str, Any]
    hardware: dict[str, Any]
    features: dict[str, bool]
    notices: list[str] = Field(default_factory=list)
    ui_languages: list[str] = Field(default_factory=lambda: ["en", "hi", "mr"])
    archive_languages: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)


class StatsResponse(BaseModel):
    documents_total: int
    documents_published: int
    manuscripts: int
    chunks: int
    embedded_chunks: int
    pages: int
    stored_bytes: int
    events: int | None = None
    media_items: int | None = None
    stories: int | None = None
    graph_nodes: int | None = None
    graph_edges: int | None = None
    rag_queries: int | None = None
    answered_queries: int | None = None
    unverified_answers: int | None = None
    integrity: dict[str, Any] | None = None
    computed_at: datetime


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserResponse"


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class UserResponse(ORMModel):
    id: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime | None = None


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    full_name: str | None = Field(default=None, max_length=160)
    role: str = "ARCHIVIST"


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #


class SourceResponse(ORMModel):
    id: str
    name: str
    source_url: str | None = None
    source_type: str | None = None
    source_domain: str | None = None
    publisher: str | None = None
    tier: str
    rights_status: str | None = None
    license: str | None = None
    retrieval_date: date | None = None


class DocumentSummary(BaseModel):
    id: str
    slug: str
    title: str
    author: str | None = None
    document_type: str
    language: str
    year: int | None = None
    document_date: date | None = None
    date_precision: str | None = None
    collection: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    source_tier: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    word_count: int | None = None
    ocr_status: str
    publication_status: str
    # Publicly exposed so no client has to guess whether text may be quoted.
    # Required, with no default: a default here would let a response omit the
    # status and quietly claim the text is verified.
    verification_status: str
    is_demo: bool = False
    thumbnail_url: str | None = None


class PageResponse(BaseModel):
    page_number: int
    image_url: str | None = None
    processed_url: str | None = None
    original_url: str | None = None
    width: int | None = None
    height: int | None = None
    ocr_engine: str | None = None
    ocr_confidence: float | None = None
    ocr_language: str | None = None
    is_approved: bool = False
    #: The UI renders the message below when a scan has no page reference.
    page_information_unavailable: bool = False
    text: str | None = None
    layout: dict[str, Any] | None = None


class DocumentDetail(DocumentSummary):
    summary: str | None = None
    location: str | None = None
    venue: str | None = None
    source_reference: str | None = None
    rights: str | None = None
    provenance: str | None = None
    editorial_note: str | None = None
    byte_size: int | None = None
    checksum_sha256: str | None = None
    storage_key: str | None = None
    processing_state: str
    embedding_status: str
    graph_status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source: SourceResponse | None = None
    collection_id: str | None = None
    topics: list[dict[str, Any]] = Field(default_factory=list)
    people: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    integrity: list[dict[str, Any]] = Field(default_factory=list)
    versions: list[dict[str, Any]] = Field(default_factory=list)


class PaginatedDocuments(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


class DocumentUploadRequest(BaseModel):
    """Metadata accompanying an upload. Everything is optional but provenance
    fields are strongly encouraged; the UI warns when they are missing."""

    title: str | None = Field(default=None, max_length=400)
    author: str | None = Field(default=None, max_length=200)
    document_type: str | None = None
    language: str | None = Field(default=None, max_length=16)
    document_date: str | None = None
    year: int | None = Field(default=None, ge=1800, le=2100)
    collection_id: str | None = None
    source_url: str | None = Field(default=None, max_length=1000)
    source_name: str | None = Field(default=None, max_length=300)
    source_type: str | None = None
    rights: str | None = None
    location: str | None = Field(default=None, max_length=300)
    venue: str | None = Field(default=None, max_length=300)
    source_reference: str | None = Field(default=None, max_length=300)
    external_id: str | None = None
    is_demo: bool = False
    editorial_note: str | None = None
    run_ocr: bool = True
    auto_publish: bool = False

    @field_validator("document_date")
    @classmethod
    def _no_free_text_date(cls, v: str | None) -> str | None:
        if v and len(v) > 40:
            raise ValueError("document_date must be an ISO date, dd.mm.yyyy or a year")
        return v


class IngestionStage(BaseModel):
    stage: str
    ok: bool
    detail: dict[str, Any] = Field(default_factory=dict)
    warning: str | None = None


class IngestionResponse(BaseModel):
    document_id: str
    state: str
    stages: list[IngestionStage]
    warnings: list[str] = Field(default_factory=list)
    job_id: str | None = None


class PageReviewRequest(BaseModel):
    corrected_text: str | None = None
    approved_by: str | None = None


class OcrRunRequest(BaseModel):
    language: str | None = None
    preset: str | None = None


class ReindexResponse(BaseModel):
    document_id: str
    chunks: int
    embedded: int
    status: str


class PublishResponse(BaseModel):
    document_id: str
    publication_status: str
    processing_state: str
    integrity: list[dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["hybrid", "keyword", "semantic", "entity"] = "hybrid"
    languages: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    topic_ids: list[str] = Field(default_factory=list)
    person_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    include_drafts: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    rerank: bool = True
    #: Returned so the UI can show exactly which index produced each hit.
    explain: bool = False


class SearchSnippet(BaseModel):
    text: str
    highlighted: list[dict[str, Any]] = Field(default_factory=list)


class SearchResultItem(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    slug: str | None = None
    score: float
    page_number: int | None = None
    page_information_unavailable: bool = False
    section: str | None = None
    language: str
    snippet: SearchSnippet
    source_url: str | None = None
    source_reference: str | None = None
    source_name: str | None = None
    source_tier: str | None = None
    document_type: str | None = None
    year: int | None = None
    verification_status: str = "verified_primary"
    quote_verified: bool = True
    provenance_warning: str | None = None
    diagnostics: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    total: int
    results: list[SearchResultItem]
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    suggestions: dict[str, list[dict[str, Any]]] | None = None
    notices: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# rag
# --------------------------------------------------------------------------- #


class RagRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1500)
    mode: Literal["grounded", "explore"] = "grounded"
    document_ids: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    max_evidence: int = Field(default=6, ge=2, le=12)
    min_evidence: int = Field(default=2, ge=1, le=6)
    answer_language: str | None = None
    persist: bool = True
    session_key: str | None = None


class EvidenceItem(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    slug: str | None = None
    page_number: int | None = None
    page_information_unavailable: bool = False
    section: str | None = None
    snippet: str
    score: float
    source_url: str | None = None
    source_reference: str | None = None
    source_name: str | None = None
    source_tier: str | None = None
    document_type: str | None = None
    year: int | None = None
    language: str = "en"
    retrieval: str | None = None
    verification_status: str = "verified_primary"
    quote_verified: bool = True
    provenance_warning: str | None = None


class CitationItem(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    page_number: int | None = None
    page_information_unavailable: bool = False
    source_url: str | None = None
    source_reference: str | None = None
    quote: str | None = None
    verified: bool = True
    verification_status: str = "verified_primary"
    provenance_warning: str | None = None


class RagResponse(BaseModel):
    query_id: str | None = None
    answer: str
    answer_kind: str
    answer_language: str
    refused: bool
    # A sentence a reader can be shown. The machine-readable token is kept
    # separately as `refusal_code` so clients never have to map codes to prose.
    refusal_reason: str | None = None
    refusal_code: str | None = None
    groundedness: float
    evidence: list[EvidenceItem] = Field(default_factory=list)
    citations: list[CitationItem] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    conflicting_evidence: bool = False
    conflict_note: str | None = None
    providers: dict[str, str] = Field(default_factory=dict)
    duration_ms: int
    #: Rendered verbatim under the answer when nothing could be verified.
    disclaimer: str | None = None


class RagHistoryItem(BaseModel):
    id: str
    question: str
    answer: str
    answer_kind: str
    refused: bool
    groundedness: float
    created_at: datetime
    citation_count: int = 0
    evidence_count: int = 0


# --------------------------------------------------------------------------- #
# graph + timeline
# --------------------------------------------------------------------------- #


class GraphNodeOut(BaseModel):
    id: str
    type: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)
    degree: int = 0
    verified: bool = True


class GraphEdgeOut(BaseModel):
    id: str
    source: str
    target: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    verified: bool = True
    provenance: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    center: str | None = None
    depth: int = 1
    backend: str
    truncated: bool = False
    notices: list[str] = Field(default_factory=list)


class GraphStatsResponse(BaseModel):
    backend: str
    nodes: int
    edges: int
    verified_edges: int
    pending_edges: int
    by_type: dict[str, int] = Field(default_factory=dict)


class GraphSearchResponse(BaseModel):
    query: str
    nodes: list[GraphNodeOut]
    backend: str


class EventOut(BaseModel):
    id: str
    title: str
    event_date: date | None = None
    year: int | None = None
    date_precision: str | None = None
    description: str | None = None
    place: str | None = None
    event_type: str | None = None
    document_count: int = 0
    person_count: int = 0
    # The weakest verification status among the published records behind this
    # event. An event is only as trustworthy as the least-checked text it rests on.
    verification_status: str = "unverified_secondary"
    is_demo: bool = False


class TimelineResponse(BaseModel):
    events: list[EventOut]
    year_from: int | None = None
    year_to: int | None = None
    total: int
    #: Years are returned so the UI can render gaps rather than guessing.
    years_with_no_data: list[int] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class TopicOut(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    document_count: int = 0
    verified: bool = True


class PersonOut(BaseModel):
    id: str
    slug: str
    name: str
    birth_year: int | None = None
    death_year: int | None = None
    summary: str | None = None
    document_count: int = 0
    verified: bool = True


# --------------------------------------------------------------------------- #
# media
# --------------------------------------------------------------------------- #


class MediaOut(BaseModel):
    id: str
    title: str
    media_type: str
    url: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    date: DateField | None = None
    description: str | None = None
    source_name: str | None = None
    rights: str | None = None
    is_demo: bool = False
    transcript_available: bool = False
    transcript_language: str | None = None


class MediaListResponse(BaseModel):
    items: list[MediaOut]
    total: int
    limit: int
    offset: int


class TranscriptSegment(BaseModel):
    index: int
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None
    speaker: str | None = None
    chunk_id: str | None = None


class TranscriptResponse(BaseModel):
    media_id: str
    language: str
    engine: str
    reviewed: bool
    segments: list[TranscriptSegment]
    transcript: str | None = None
    notices: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# stories
# --------------------------------------------------------------------------- #


class StoryChapterOut(BaseModel):
    id: str
    position: int
    title: str
    body: str
    media_id: str | None = None
    document_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None


class StoryOut(BaseModel):
    id: str
    slug: str
    title: str
    subtitle: str | None = None
    summary: str | None = None
    hero_image_url: str | None = None
    reading_minutes: int | None = None
    chapters: list[StoryChapterOut] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    published: bool = False
    updated_at: datetime | None = None


class ResearchItemOut(BaseModel):
    id: str
    question: str
    answer: str
    answer_kind: str
    groundedness: float
    citations: list[CitationItem] = Field(default_factory=list)
    created_at: datetime


# --------------------------------------------------------------------------- #
# jobs + admin
# --------------------------------------------------------------------------- #


class JobOut(BaseModel):
    id: str
    job_type: str
    status: str
    priority: int
    progress: float
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_by: str | None = None


class JobListResponse(BaseModel):
    items: list[JobOut]
    total: int
    queue_depth: int
    workers_alive: int
    backend: str


class EntityReviewOut(BaseModel):
    id: str
    relation: str
    from_type: str
    from_id: str
    from_label: str | None = None
    to_type: str
    to_id: str
    to_label: str | None = None
    confidence: float | None = None
    status: str
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    note: str | None = None


class RelationshipDecision(BaseModel):
    status: Literal["verified", "rejected", "pending"]
    note: str | None = None


class StoryUpsert(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    slug: str | None = Field(default=None, max_length=160)
    subtitle: str | None = Field(default=None, max_length=300)
    summary: str | None = None
    hero_media_id: str | None = None
    reading_minutes: int | None = Field(default=None, ge=1, le=240)
    published: bool = False
    topic_ids: list[str] = Field(default_factory=list)


class StoryChapterUpsert(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)
    position: int = Field(ge=0, le=500)
    media_id: str | None = None
    document_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None


class IntegrityReportOut(BaseModel):
    object_key: str
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    ok: bool
    size: int | None = None
    checked_at: datetime | None = None
    detail: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    request_id: str | None = None


TokenResponse.model_rebuild()

#: Rendered verbatim whenever a scan carries no page reference. The archive
#: never invents a page number to fill the gap.
PAGE_INFORMATION_UNAVAILABLE = "Page information unavailable in indexed source."
