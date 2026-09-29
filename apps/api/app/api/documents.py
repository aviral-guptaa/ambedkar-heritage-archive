"""Document endpoints: public reading, plus ingestion and curation for staff.

Public routes only ever expose published documents. Drafts, their OCR output and
their provenance notes are visible exclusively to authenticated users with the
matching permission, so a half-digitised scan can never be mistaken for a
published one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentPrincipal, DbSession, not_found
from app.core.logging import get_logger
from app.core.security import has_permission, require_permission
from app.models.archive import (
    Collection,
    Document,
    DocumentChunk,
    DocumentMetadata,
    DocumentPage,
    DocumentVersion,
    Source,
)
from app.models.enums import PublicationStatus
from app.models.knowledge import EventDocument, TopicLink
from app.models.ops import IntegrityCheck
from app.providers.storage import get_object_store
from app.schemas import (
    DocumentDetail,
    DocumentSummary,
    IngestionResponse,
    OcrRunRequest,
    PageResponse,
    PageReviewRequest,
    PaginatedDocuments,
    PublishResponse,
    ReindexResponse,
)
from app.services import ingestion as ing
from app.services.audit import record as record_audit
from app.services.preservation import verify_document

log = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


# --------------------------------------------------------------------------- #
# serialisation
# --------------------------------------------------------------------------- #


def _can_see_draft(principal) -> bool:
    return principal.is_authenticated and has_permission(
        principal.user, "document:read"
    )


def _visible_statuses(principal) -> list[str]:
    if _can_see_draft(principal):
        return [
            PublicationStatus.PUBLISHED.value,
            PublicationStatus.DRAFT.value,
            PublicationStatus.IN_REVIEW.value,
        ]
    return [PublicationStatus.PUBLISHED.value]


def _summary(doc: Document, source: Source | None, collection_title: str | None) -> DocumentSummary:
    store = get_object_store()
    first_page = getattr(doc, "pages", None)
    thumb = None
    if first_page:
        page = sorted(first_page, key=lambda p: p.page_number)[0]
        if page.thumbnail_key:
            thumb = store.url_for(page.thumbnail_key)
    return DocumentSummary(
        id=doc.id,
        slug=doc.slug,
        title=doc.title,
        author=doc.author_display,
        document_type=doc.document_type,
        language=doc.language,
        year=doc.year,
        document_date=doc.document_date,
        date_precision=doc.date_precision,
        collection=collection_title,
        source_name=source.name if source else None,
        source_url=source.source_url if source else doc.source_url,
        source_tier=source.tier if source else None,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        word_count=doc.word_count,
        ocr_status=doc.ocr_status,
        publication_status=doc.publication_status,
        verification_status=str(doc.verification_status),
        is_demo=doc.is_demo,
        thumbnail_url=thumb,
    )


def _load_document(db: Session, identifier: str, principal) -> Document:
    doc = db.scalar(
        select(Document).where((Document.id == identifier) | (Document.slug == identifier))
    )
    if doc is None or doc.deleted_at is not None:
        raise not_found("That document")
    if doc.publication_status != PublicationStatus.PUBLISHED and not _can_see_draft(principal):
        raise not_found("That document")
    return doc


def _facets(db: Session, principal) -> dict[str, list[dict[str, Any]]]:
    """Filter options with real counts, computed from published rows only."""
    criteria = (
        Document.deleted_at.is_(None),
        Document.publication_status.in_(_visible_statuses(principal)),
    )
    languages = db.execute(
        select(Document.language, func.count())
        .where(*criteria, Document.language.isnot(None))
        .group_by(Document.language)
        .order_by(func.count().desc())
    ).all()
    types = db.execute(
        select(Document.document_type, func.count())
        .where(*criteria, Document.document_type.isnot(None))
        .group_by(Document.document_type)
        .order_by(func.count().desc())
    ).all()
    years = db.execute(select(func.min(Document.year), func.max(Document.year)).where(*criteria)).one()
    return {
        "languages": [
            {"value": lang, "label": lang, "count": int(n)} for lang, n in languages if lang
        ],
        "document_types": [
            {"value": t, "label": str(t).replace("_", " ").title(), "count": int(n)}
            for t, n in types
            if t
        ],
        "year_range": {"from": years[0], "to": years[1]},
    }


# --------------------------------------------------------------------------- #
# public reads
# --------------------------------------------------------------------------- #


@router.get("", response_model=PaginatedDocuments, summary="List documents")
def list_documents(
    db: DbSession,
    principal: CurrentPrincipal,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    language: list[str] = Query(default=[]),
    document_type: list[str] = Query(default=[]),
    collection_id: list[str] = Query(default=[]),
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str = Query("relevance", pattern="^(relevance|date|year|title)$"),
    q: str | None = None,
) -> PaginatedDocuments:
    statuses = _visible_statuses(principal)
    stmt = select(Document).where(
        Document.deleted_at.is_(None), Document.publication_status.in_(statuses)
    )
    if language:
        stmt = stmt.where(Document.language.in_(language))
    if document_type:
        stmt = stmt.where(Document.document_type.in_(document_type))
    if collection_id:
        stmt = stmt.where(Document.collection_id.in_(collection_id))
    if year_from is not None:
        stmt = stmt.where(Document.year.isnot(None), Document.year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Document.year.isnot(None), Document.year <= year_to)
    if q:
        stmt = stmt.where(Document.title.ilike(f"%{q.strip()}%"))

    order = {
        "date": Document.document_date.desc().nullslast(),
        "year": Document.year.desc().nullslast(),
        "title": Document.title.asc(),
        "relevance": Document.ocr_status.asc(),  # approved OCR first
    }[sort]
    total = db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ) or 0
    stmt = stmt.order_by(order, Document.id).limit(limit).offset(offset)

    rows = db.scalars(stmt).all()
    source_ids = {d.source_id for d in rows if d.source_id}
    sources = {s.id: s for s in db.scalars(select(Source).where(Source.id.in_(source_ids))).all()} if source_ids else {}
    collection_ids = {d.collection_id for d in rows if d.collection_id}
    collections = (
        {
            c.id: c.title
            for c in db.scalars(select(Collection).where(Collection.id.in_(collection_ids))).all()
        }
        if collection_ids
        else {}
    )
    return PaginatedDocuments(
        items=[
            _summary(d, sources.get(d.source_id) if d.source_id else None, collections.get(d.collection_id) if d.collection_id else None)
            for d in rows
        ],
        total=int(total),
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < int(total),
    )


@router.get("/facets", summary="Filter options with real counts")
def facets(db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    return _facets(db, principal)


@router.get("/{identifier}", response_model=DocumentDetail, summary="Document detail")
def get_document(identifier: str, db: DbSession, principal: CurrentPrincipal) -> DocumentDetail:
    doc = _load_document(db, identifier, principal)
    source = db.get(Source, doc.source_id) if doc.source_id else None
    collection_title = None
    if doc.collection_id:
        collection = db.get(Collection, doc.collection_id)
        collection_title = collection.title if collection else None

    metadata = {
        m.key: m.value
        for m in db.scalars(
            select(DocumentMetadata).where(DocumentMetadata.document_id == doc.id)
        ).all()
    }
    topic_rows = [
        {
            "topic_id": link.topic_id,
            "name": link.topic.name if link.topic else None,
            "slug": link.topic.slug if link.topic else None,
            "weight": link.weight,
            "source": link.source,
        }
        for link in doc.topics
    ]
    people = [
        {
            "person_id": link.person_id,
            "name": link.person.canonical_name if link.person else None,
            "role": link.role,
        }
        for link in doc.people
    ]
    events = [
        {
            "event_id": link.event_id,
            "title": link.event.title if link.event else None,
            "date": link.event.event_date if link.event else None,
            "relation": link.relation,
        }
        for link in doc.events
    ]

    versions = [
        {
            "version": v.version,
            "note": v.note,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "is_current": v.is_current,
            "sha256": v.checksum_sha256,
        }
        for v in db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version.desc())
        ).all()
    ]
    checks = [
        {
            "scope": c.scope,
            "object_key": c.object_key,
            "status": c.status,
            "checked_at": c.checked_at.isoformat() if c.checked_at else None,
        }
        for c in db.scalars(
            select(IntegrityCheck)
            .where(IntegrityCheck.document_id == doc.id)
            .order_by(IntegrityCheck.checked_at.desc())
            .limit(20)
        ).all()
    ]

    base = _summary(doc, source, collection_title)
    return DocumentDetail(
        **base.model_dump(),
        summary=doc.summary,
        location=doc.location_text,
        venue=doc.venue,
        source_reference=doc.source_reference,
        rights=doc.rights,
        provenance=doc.provenance,
        editorial_note=doc.editorial_note if principal.is_authenticated or not doc.is_demo else None,
        byte_size=doc.byte_size,
        checksum_sha256=doc.checksum_sha256,
        storage_key=None,
        processing_state=doc.processing_state,
        embedding_status=doc.embedding_status,
        graph_status=doc.graph_status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        source=source,
        collection_id=doc.collection_id,
        topics=topic_rows,
        people=people,
        events=events,
        metadata=metadata,
        integrity=checks,
        versions=versions,
    )


@router.get("/{identifier}/summary", summary="Grounded 30-second understanding")
def document_summary(identifier: str, db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    """Summarise one record using only that record's own indexed text.

    Extractive, not generated: every line is a sentence the archive already
    holds, so this needs no model and cannot introduce a fact, date, or
    quotation the record does not contain.
    """
    from app.services.docsummary import build_summary

    doc = _load_document(db, identifier, principal)
    return build_summary(db, doc)


@router.get("/{identifier}/pages", response_model=list[PageResponse], summary="Page images and text")
def get_pages(
    identifier: str, db: DbSession, principal: CurrentPrincipal
) -> list[PageResponse]:
    doc = _load_document(db, identifier, principal)
    store = get_object_store()
    pages = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == doc.id)
        .order_by(DocumentPage.page_number)
    ).all()
    out: list[PageResponse] = []
    for page in pages:
        text = page.ocr_corrected_text or page.ocr_text
        out.append(
            PageResponse(
                page_number=page.page_number,
                image_url=store.url_for(page.processed_key) if page.processed_key else None,
                processed_url=store.url_for(page.processed_key) if page.processed_key else None,
                original_url=store.url_for(page.original_key) if page.original_key else None,
                width=page.width,
                height=page.height,
                ocr_engine=page.ocr_engine,
                ocr_confidence=page.ocr_confidence,
                ocr_language=page.ocr_language,
                is_approved=page.is_approved,
                page_information_unavailable=page.page_number is None or doc.page_count is None,
                text=text,
                layout=page.layout,
            )
        )
    return out


@router.get(
    "/{identifier}/chunks",
    summary="Chunk-level view (admin, for indexing review)",
)
def get_chunks(identifier: str, db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    if not principal.is_authenticated:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sign in to view chunks.")
    doc = _load_document(db, identifier, principal)
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == doc.id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    return {
        "total": len(chunks),
        "chunks": [
            {
                "id": c.id,
                "index": c.chunk_index,
                "page_number": c.page_number,
                "section": c.section,
                "language": c.language,
                "token_count": c.token_count,
                "embedded": c.embedding is not None,
                "embedding_model": c.embedding_model,
                "text": c.text,
            }
            for c in chunks
        ],
    }


@router.get(
    "/{identifier}/text",
    summary="Readable text of a published document",
)
def get_text(identifier: str, db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    """The document's stored text, for reading.

    This is the public counterpart to the admin chunk view: same text, but
    assembled for a reader and carrying its provenance on every part, so the
    interface cannot show unchecked text without also showing that it is
    unchecked. ``_load_document`` already refuses to serve an unpublished
    document to a public visitor.
    """
    doc = _load_document(db, identifier, principal)
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == doc.id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    status = str(doc.verification_status)
    parts = [
        {
            "index": c.chunk_index,
            "section": c.section,
            "language": c.language,
            "page_number": c.page_number,
            # The interface shows this phrase in place of a page number, rather
            # than implying a pagination the source does not have.
            "page_information_unavailable": c.page_number is None,
            "text": c.text,
        }
        for c in chunks
    ]
    note = None
    if status != "verified_primary":
        note = (
            "This is an unverified secondary text. It is reproduced so it can be read and "
            "searched, but it has not been checked against the archival original and must "
            "not be quoted as Dr. Ambedkar's words."
        )
    return {
        "document_id": doc.id,
        "slug": doc.slug,
        "title": doc.title,
        "language": doc.language,
        "verification_status": status,
        "quote_verified": status == "verified_primary",
        "provenance_warning": note,
        "source_url": doc.source_url,
        "source_reference": doc.source_reference,
        "total": len(parts),
        "parts": parts,
    }


# --------------------------------------------------------------------------- #
# ingestion (staff)
# --------------------------------------------------------------------------- #


@router.post(
    "",
    response_model=IngestionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a file",
)
async def upload_document(
    db: DbSession,
    principal: CurrentPrincipal,
    file: UploadFile = File(..., description="PDF, scan, text, audio or video file"),
    title: str | None = Form(default=None),
    author: str | None = Form(default=None),
    document_type: str | None = Form(default=None),
    language: str | None = Form(default=None),
    document_date: str | None = Form(default=None),
    year: int | None = Form(default=None),
    collection_id: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    source_name: str | None = Form(default=None),
    source_type: str | None = Form(default=None),
    rights: str | None = Form(default=None),
    location: str | None = Form(default=None),
    venue: str | None = Form(default=None),
    source_reference: str | None = Form(default=None),
    external_id: str | None = Form(default=None),
    is_demo: bool = Form(default=False),
    editorial_note: str | None = Form(default=None),
    run_ocr: bool = Form(default=True),
    auto_publish: bool = Form(default=False),
) -> IngestionResponse:
    if not principal.is_authenticated or not has_permission(principal.user, "document:write"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role cannot upload documents to this archive.",
        )
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit.",
        )
    try:
        result = ing.ingest_document(
            db,
            data,
            file.filename or "upload",
            title=title,
            author=author,
            document_type=document_type,
            language=language,
            document_date=document_date,
            year=year,
            collection_id=collection_id,
            source_url=source_url,
            source_name=source_name,
            source_type=source_type,
            rights=rights,
            location=location,
            venue=venue,
            source_reference=source_reference,
            external_id=external_id,
            is_demo=is_demo,
            editorial_note=editorial_note,
            created_by=principal.id,
            run_ocr_if_needed=run_ocr,
            auto_publish=auto_publish,
        )
    except ing.IngestionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    job_id = None
    if not auto_publish:
        from app.services.tasks import enqueue_job

        job_id = enqueue_job(
            db,
            "finalise",
            {"document_id": result.document_id},
            created_by=principal.id,
        )
    log.info(
        "document ingested",
        document_id=result.document_id,
        state=result.state,
        warnings=len(result.warnings),
        by=principal.id,
    )
    return IngestionResponse(**result.as_dict(), job_id=job_id)


@router.post(
    "/{identifier}/ocr",
    summary="Run OCR on a page",
    dependencies=[Depends(require_permission("ocr:run"))],
)
def run_page_ocr(
    identifier: str,
    db: DbSession,
    principal: CurrentPrincipal,
    page_number: int = Query(..., ge=1),
    payload: OcrRunRequest | None = None,
) -> dict[str, Any]:
    doc = _load_document(db, identifier, principal)
    page = db.scalar(
        select(DocumentPage).where(
            DocumentPage.document_id == doc.id, DocumentPage.page_number == page_number
        )
    )
    if page is None:
        raise not_found(f"Page {page_number}")
    try:
        result = ing.run_ocr_stage(
            db,
            doc,
            page,
            language=(payload.language if payload else None),
            preset=(payload.preset if payload else "standard"),
        )
    except ing.IngestionError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return {
        "document_id": doc.id,
        "page_number": page.page_number,
        "engine": result.engine,
        "language": result.language,
        "confidence": result.confidence,
        "duration_ms": result.duration_ms,
        "text": result.raw_text,
        "corrected_text": result.corrected_text,
        "status": doc.ocr_status,
        "review_required": True,
    }


@router.post("/{identifier}/pages/{page_number}/review", summary="Approve or correct a page")
def review_page(
    identifier: str,
    page_number: int,
    payload: PageReviewRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> dict[str, Any]:
    if not principal.is_authenticated or not has_permission(principal.user, "ocr:approve"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Your role cannot approve OCR."
        )
    doc = _load_document(db, identifier, principal)
    page = db.scalar(
        select(DocumentPage).where(
            DocumentPage.document_id == doc.id, DocumentPage.page_number == page_number
        )
    )
    if page is None:
        raise not_found(f"Page {page_number}")
    record_audit(
        db,
        action="ocr.approve",
        actor=principal.user,
        entity_type="page",
        entity_id=page.id,
        detail={
            "document_id": doc.id,
            "page_number": page_number,
            # The correction itself is not copied into the log: it is already
            # preserved as a page version, and the log only needs to point at it.
            "corrected": payload.corrected_text is not None,
        },
        request_id=principal.request_id,
    )
    ing.approve_page(db, page, corrected_text=payload.corrected_text, approved_by=principal.id)
    # Corrections change the text, so the index has to be rebuilt from it.
    ok, chunks, embedded = ing.index_document(db, doc)
    db.commit()
    return {
        "document_id": doc.id,
        "page_number": page_number,
        "approved": True,
        "reindexed": {"ok": ok, "chunks": chunks, "embedded": embedded},
    }


@router.post("/{identifier}/reindex", response_model=ReindexResponse, summary="Rebuild text and vectors")
def reindex(
    identifier: str, db: DbSession, principal: CurrentPrincipal
) -> ReindexResponse:
    if not principal.is_authenticated or not has_permission(principal.user, "document:write"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")
    doc = _load_document(db, identifier, principal)
    record_audit(
        db,
        action="document.reindex",
        actor=principal.user,
        entity_type="document",
        entity_id=doc.id,
        detail={"slug": doc.slug},
        request_id=principal.request_id,
    )
    db.commit()
    ok, chunks, embedded = ing.reindex_document(db, doc)
    return ReindexResponse(
        document_id=doc.id, chunks=chunks, embedded=embedded, status=doc.embedding_status
    )


@router.post("/{identifier}/publish", response_model=PublishResponse, summary="Publish a document")
def publish(
    identifier: str, db: DbSession, principal: CurrentPrincipal
) -> PublishResponse:
    if not principal.is_authenticated or not has_permission(principal.user, "document:publish"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted to publish.")
    doc = _load_document(db, identifier, principal)
    if doc.chunk_count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document has no indexed text, so it cannot be published yet.",
        )
    record_audit(
        db,
        action="document.publish",
        actor=principal.user,
        entity_type="document",
        entity_id=doc.id,
        detail={
            "slug": doc.slug,
            "title": doc.title,
            "verification_status": str(doc.verification_status),
            "chunk_count": doc.chunk_count,
        },
        request_id=principal.request_id,
    )
    ing.publish_document(db, doc)
    reports = verify_document(db, doc) if settings.integrity_auto_check_on_publish else []
    db.commit()
    return PublishResponse(
        document_id=doc.id,
        publication_status=doc.publication_status,
        processing_state=doc.processing_state,
        integrity=[r.as_dict() for r in reports],
    )


@router.post("/{identifier}/withdraw", summary="Withdraw a published document")
def withdraw(identifier: str, db: DbSession, principal: CurrentPrincipal) -> dict[str, str]:
    if not principal.is_authenticated or not has_permission(principal.user, "document:publish"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")
    doc = _load_document(db, identifier, principal)
    record_audit(
        db,
        action="document.withdraw",
        actor=principal.user,
        entity_type="document",
        entity_id=doc.id,
        detail={"slug": doc.slug, "title": doc.title},
        request_id=principal.request_id,
    )
    ing.soft_delete_document(db, doc)
    return {"detail": f"{doc.title} is no longer public."}


@router.get("/{identifier}/versions", summary="Preservation history")
def versions(identifier: str, db: DbSession, principal: CurrentPrincipal) -> list[dict[str, Any]]:
    doc = _load_document(db, identifier, principal)
    rows = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.version.desc())
    ).all()
    return [
        {
            "version": v.version,
            "note": v.note,
            "sha256": v.checksum_sha256,
            "byte_size": v.byte_size,
            "mime_type": v.mime_type,
            "is_current": v.is_current,
            "created_at": v.created_at,
        }
        for v in rows
    ]


@router.get("/{identifier}/verify", summary="Re-verify a document's preserved objects")
def verify(identifier: str, db: DbSession, principal: CurrentPrincipal) -> list[dict[str, Any]]:
    if not principal.is_authenticated or not has_permission(principal.user, "integrity:run"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")
    doc = _load_document(db, identifier, principal)
    return [r.as_dict() for r in verify_document(db, doc)]


@router.get("/{identifier}/translate", summary="Machine-translate a document")
def translate(
    identifier: str,
    db: DbSession,
    principal: CurrentPrincipal,
    target_language: str = Query(..., min_length=2, max_length=8),
) -> dict[str, Any]:
    if not principal.is_authenticated or not has_permission(principal.user, "document:write"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")
    doc = _load_document(db, identifier, principal)
    return ing.translate_document(db, doc, target_language)
