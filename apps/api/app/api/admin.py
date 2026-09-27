"""Administrative endpoints: jobs, OCR review queue, entity review, audit log.

Everything here requires an authenticated principal with a specific permission.
The admin application is a separate origin and never shares a session with the
public site.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentPrincipal, DbSession
from app.core.security import has_permission, require_any_permission, require_permission
from app.models.access import AuditLog
from app.models.archive import Collection, Document, DocumentPage, Source
from app.models.enums import JobState
from app.models.knowledge import GraphEdge, GraphNode, Relationship
from app.models.ops import ProcessingJob
from app.providers.jobs import get_queue
from app.schemas import EntityReviewOut, JobListResponse, JobOut, RelationshipDecision
from app.api.documents import _summary
from app.services.audit import record as record_audit
from app.services.indexing import embedding_index_status
from app.services.ingestion import run_ocr_stage

# Gated at the router so an individual handler cannot accidentally ship without
# a check: an anonymous or read-only caller cannot reach any admin endpoint.
# Handlers narrow this further with _require() for their specific action.
router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[
        Depends(
            require_any_permission(
                "job:read", "job:retry", "ocr:run", "ocr:approve", "graph:review", "audit:read"
            )
        )
    ],
)


def _require(principal, permission: str) -> None:
    if not principal.is_authenticated or not has_permission(principal.user, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role ({principal.role}) cannot perform this action.",
        )


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #


@router.get("/documents", summary="List documents, including drafts")
def admin_list_documents(
    db: DbSession,
    principal: CurrentPrincipal,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str | None = None,
    publication_status: str | None = None,
    verification_status: str | None = None,
    sort: str = Query("updated", pattern="^(updated|created|date|title|year)$"),
) -> dict[str, Any]:
    """The working list a curator edits from.

    The public list endpoint only ever shows published records to a reader, so
    it cannot be used to find a draft that is waiting to be published. This one
    is therefore deliberately separate, and requires document:read.
    """
    _require(principal, "document:read")

    stmt = select(Document).where(Document.deleted_at.is_(None))
    if publication_status:
        stmt = stmt.where(Document.publication_status == publication_status)
    if verification_status:
        stmt = stmt.where(Document.verification_status == verification_status)
    if q:
        # The public search only matches titles, which makes it useless for
        # finding a record when you remember the speaker or the venue instead.
        term = f"%{q.strip()}%"
        stmt = stmt.where(
            Document.title.ilike(term)
            | Document.author_display.ilike(term)
            | Document.venue.ilike(term)
            | Document.id.ilike(term)
        )

    order = {
        "created": Document.created_at.desc(),
        "updated": Document.updated_at.desc().nullslast(),
        "date": Document.document_date.desc().nullslast(),
        "title": Document.title.asc(),
        "year": Document.year.desc().nullslast(),
    }[sort]
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.order_by(order, Document.id).limit(limit).offset(offset)).all()

    source_ids = {d.source_id for d in rows if d.source_id}
    sources = {
        s.id: s
        for s in db.scalars(select(Source).where(Source.id.in_(source_ids))).all()
    } if source_ids else {}
    collections = {
        c.id: c.title
        for c in db.scalars(
            select(Collection).where(
                Collection.id.in_({d.collection_id for d in rows if d.collection_id})
            )
        ).all()
    } if any(d.collection_id for d in rows) else {}

    return {
        "total": total,
        "items": [
            _summary(d, sources.get(d.source_id) if d.source_id else None, collections.get(d.collection_id))
            for d in rows
        ],
    }


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #


@router.get("/jobs", response_model=JobListResponse, summary="Background jobs")
def list_jobs(
    db: DbSession,
    principal: CurrentPrincipal,
    job_type: str | None = None,
    state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> JobListResponse:
    _require(principal, "job:read")
    stmt = select(ProcessingJob)
    if job_type:
        stmt = stmt.where(ProcessingJob.kind == job_type)
    if state:
        stmt = stmt.where(ProcessingJob.state == state)
    rows = db.scalars(stmt.order_by(ProcessingJob.created_at.desc()).limit(limit)).all()
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    queue = get_queue()
    return JobListResponse(
        items=[
            JobOut(
                id=j.id,
                job_type=str(j.kind),
                status=str(j.state),
                priority=0,
                progress=round(j.progress / 100.0, 4),
                payload=j.payload or {},
                result=j.result,
                error=j.error,
                attempts=j.attempts,
                created_at=j.created_at,
                started_at=None,
                finished_at=None,
                created_by=j.created_by,
            )
            for j in rows
        ],
        total=int(total),
        queue_depth=queue.queue_depth(),
        workers_alive=queue.workers_alive(),
        backend=queue.name,
    )


@router.post("/jobs/{job_id}/retry", summary="Requeue a failed job")
def retry_job(job_id: str, db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    _require(principal, "job:retry")
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    previous_state = str(job.state)
    job.state = JobState.QUEUED
    job.error = None
    job.error_type = None
    job.progress = 0
    record_audit(
        db,
        action="job.retry",
        actor=principal.user,
        entity_type="job",
        entity_id=job.id,
        detail={"from_state": previous_state, "kind": str(job.kind)},
        request_id=principal.request_id,
    )
    db.commit()
    get_queue().enqueue(job.id)
    return {"detail": f"Job {job.id} requeued.", "job_id": job.id}


@router.get("/jobs/index-status", summary="Embedding index coverage")
def index_status(db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    _require(principal, "document:read")
    from app.services.indexing import stale_vector_count

    payload = embedding_index_status(db)
    payload["stale_vectors"] = stale_vector_count(db)
    return payload


# --------------------------------------------------------------------------- #
# OCR review
# --------------------------------------------------------------------------- #


@router.get("/ocr/queue", summary="Pages awaiting human review")
def ocr_queue(
    db: DbSession,
    principal: CurrentPrincipal,
    limit: int = Query(50, ge=1, le=200),
    document_id: str | None = None,
) -> dict[str, Any]:
    _require(principal, "ocr:approve")
    stmt = (
        select(DocumentPage, Document)
        .join(Document, Document.id == DocumentPage.document_id)
        .where(DocumentPage.is_approved.is_(False), Document.deleted_at.is_(None))
    )
    if document_id:
        stmt = stmt.where(DocumentPage.document_id == document_id)
    rows = db.execute(stmt.order_by(Document.updated_at.desc()).limit(limit)).all()
    return {
        "total": len(rows),
        "items": [
            {
                "document_id": doc.id,
                "document_title": doc.title,
                "page_number": page.page_number,
                "ocr_engine": page.ocr_engine,
                "ocr_confidence": page.ocr_confidence,
                "ocr_language": page.ocr_language,
                "document_ocr_status": str(doc.ocr_status),
                "text": (page.ocr_corrected_text or page.ocr_text or "")[:4000],
                "layout": page.layout,
            }
            for page, doc in rows
        ],
    }


@router.post("/ocr/reprocess", summary="Re-run OCR on a page")
def reprocess(
    db: DbSession,
    principal: CurrentPrincipal,
    document_id: str = Query(...),
    page_number: int = Query(..., ge=1),
    language: str | None = None,
    preset: str = "standard",
) -> dict[str, Any]:
    _require(principal, "ocr:run")
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    page = db.scalar(
        select(DocumentPage).where(
            DocumentPage.document_id == document_id, DocumentPage.page_number == page_number
        )
    )
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Page {page_number} not found."
        )
    try:
        result = run_ocr_stage(db, doc, page, language=language, preset=preset)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"OCR could not run: {exc}",
        ) from exc
    return {
        "document_id": doc.id,
        "page_number": page_number,
        "engine": result.engine,
        "confidence": result.confidence,
        "duration_ms": result.duration_ms,
        "status": str(doc.ocr_status),
    }


@router.get("/ocr/engines", summary="OCR engines and languages")
def ocr_engines() -> dict[str, Any]:
    from app.providers.ocr import ocr_capability

    return ocr_capability()


# --------------------------------------------------------------------------- #
# entity / relationship review
# --------------------------------------------------------------------------- #


@router.get("/review/relationships", response_model=list[EntityReviewOut], summary="Pending relationships")
def pending_relationships(
    db: DbSession,
    principal: CurrentPrincipal,
    status_filter: str = Query("pending", pattern="^(pending|verified|rejected|all)$"),
    limit: int = Query(100, ge=1, le=500),
) -> list[EntityReviewOut]:
    _require(principal, "graph:review")
    stmt = select(Relationship)
    if status_filter != "all":
        stmt = stmt.where(Relationship.status == status_filter)
    rows = db.scalars(stmt.order_by(Relationship.created_at.desc()).limit(limit)).all()
    node_ids: set[str] = set()
    for r in rows:
        node_ids.update(
            [f"{r.from_type}:{r.from_id}", f"{r.to_type}:{r.to_id}"]
        )
    labels = {
        n.id: n.label
        for n in db.scalars(select(GraphNode).where(GraphNode.id.in_(node_ids))).all()
    } if node_ids else {}
    return [
        EntityReviewOut(
            id=r.id,
            relation=str(r.relation),
            from_type=str(r.from_type),
            from_id=r.from_id,
            from_label=labels.get(f"{r.from_type}:{r.from_id}"),
            to_type=str(r.to_type),
            to_id=r.to_id,
            to_label=labels.get(f"{r.to_type}:{r.to_id}"),
            confidence=r.confidence,
            status=r.status,
            created_at=r.created_at,
            decided_at=r.reviewed_at,
            decided_by=r.reviewed_by,
            note=(r.properties or {}).get("note"),
        )
        for r in rows
    ]


@router.post("/review/relationships/{relationship_id}", summary="Verify or reject a relationship")
def decide_relationship(
    relationship_id: str,
    payload: RelationshipDecision,
    db: DbSession,
    principal: CurrentPrincipal,
) -> dict[str, Any]:
    _require(principal, "graph:review")
    from app.services.knowledge import review_relationship

    if db.get(Relationship, relationship_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found."
        )
    updated = review_relationship(
        db, relationship_id, approve=payload.status == "verified", reviewer_id=principal.id
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found."
        )
    if payload.note:
        props = dict(updated.properties or {})
        props["note"] = payload.note
        updated.properties = props
    record_audit(
        db,
        action="graph.review",
        actor=principal.user,
        entity_type="relationship",
        entity_id=relationship_id,
        detail={
            "status": payload.status,
            "src_id": updated.src_id,
            "dst_id": updated.dst_id,
            "relation": str(updated.relation_type),
        },
        request_id=principal.request_id,
    )
    db.commit()
    return {"detail": f"Relationship {relationship_id} marked {payload.status}."}


@router.get("/review/graph-nodes", summary="Graph nodes with their degree")
def review_nodes(
    db: DbSession, principal: CurrentPrincipal, limit: int = Query(100, ge=1, le=500)
) -> list[dict[str, Any]]:
    _require(principal, "graph:review")
    rows = db.execute(
        select(GraphNode, func.count(GraphEdge.id))
        .outerjoin(GraphEdge, GraphEdge.src_id == GraphNode.id)
        .group_by(GraphNode.id)
        .order_by(func.count(GraphEdge.id).desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": n.id,
            "label": n.label,
            "entity_type": str(n.entity_type),
            "name": n.name,
            "year": n.year,
            "out_edges": int(degree),
            "properties": n.properties or {},
        }
        for n, degree in rows
    ]


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #


@router.get("/audit", summary="Audit log")
def audit_log(
    db: DbSession,
    principal: CurrentPrincipal,
    limit: int = Query(100, ge=1, le=500),
    action: str | None = None,
) -> list[dict[str, Any]]:
    _require(principal, "audit:read")
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "actor_id": r.actor_id,
            # Shown in the admin trail: a row with no actor is either a scheduled
            # action or a system promotion, and the reader must be able to tell.
            "actor_email": r.actor_email,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "detail": r.detail,
            "created_at": r.created_at,
        }
        for r in rows
    ]
