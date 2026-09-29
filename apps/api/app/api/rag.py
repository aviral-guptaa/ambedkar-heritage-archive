"""RAG endpoints: grounded research answers and the visitor's research trail.

The rules encoded here are the point of the feature:

* an answer is only returned when the retrieved evidence supports it;
* a refusal is a first-class, well-formed answer, not an error;
* every citation resolves to chunk -> document -> page -> original source, or
  says plainly that the indexed source carries no page information;
* conflicting evidence is surfaced rather than silently reconciled;
* a low groundedness score is reported to the visitor instead of being hidden.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentPrincipal, DbSession
from app.models.archive import Document
from app.models.enums import AnswerKind, PublicationStatus
from app.models.access import UserResearchItem
from app.models.ops import Citation, RagQuery
from app.schemas import (
    CitationItem,
    EvidenceItem,
    RagHistoryItem,
    RagRequest,
    RagResponse,
    ResearchItemOut,
)
from app.services.rag import UNAVAILABLE_PAGE, RAGRequest as ServiceRequest, answer_question
from app.services.search import SearchFilters

router = APIRouter(tags=["rag"])

NO_ANSWER_DISCLAIMER = (
    "This answer is assembled only from the archive's indexed sources. "
    "The archive does not generate content that is not present in a source it holds."
)


def _service_request(payload: RagRequest, principal) -> ServiceRequest:
    return ServiceRequest(
        question=payload.question,
        answer_language=payload.answer_language or "auto",
        filters=SearchFilters(
            languages=payload.languages,
            document_types=payload.document_types,
            document_ids=payload.document_ids,
            collection_ids=[],
            topic_ids=[],
            person_ids=[],
            source_ids=[],
            year_from=payload.year_from,
            year_to=payload.year_to,
            include_drafts=False,
        ),
        mode="hybrid",
        top_k=payload.max_evidence,
        session_key=payload.session_key or principal.session_key,
        user_id=principal.id,
    )


#: Why an answer was declined, in words a reader can be shown. The archive must
#: never leave the reason as an internal token, and must never imply the archive
#: lacks the topic when it simply lacks usable evidence.
_REFUSAL_REASONS: dict[str, str] = {
    "empty_question": "No question was given, so there was nothing to look for.",
    "no_evidence": (
        "The archive holds no indexed passage on this question."
    ),
    "llm_unavailable": (
        "The answer service was unavailable, so no answer was generated. Nothing was "
        "invented to fill the gap."
    ),
    "threshold": (
        "The archive retrieved passages, but none of them is about this question. "
        "Rather than stretch an unrelated passage into an answer, it declines."
    ),
}


def _trail_field(evidence: list[dict], chunk_id: str | None, key: str):
    """Read one field of a stored evidence item by chunk id."""
    for item in evidence:
        if item.get("chunk_id") == chunk_id:
            return item.get(key)
    return None


def _trail_page(evidence: list[dict], chunk_id: str | None) -> int | None:
    page = _trail_field(evidence, chunk_id, "page_number")
    if page is None:
        page = _trail_field(evidence, chunk_id, "page")
    return page if isinstance(page, int) else None


def _response(result, payload: RagRequest) -> RagResponse:
    citations: list[CitationItem] = []
    for c in result.citations:
        evidence = next((e for e in result.evidence if e.chunk_id == c.chunk_id), None)
        citations.append(
            CitationItem(
                marker=citations.__len__() + 1,
                chunk_id=c.chunk_id or "",
                document_id=c.document_id or "",
                document_title=evidence.document_title if evidence else "",
                page_number=evidence.page_number if evidence else None,
                page_information_unavailable=evidence is None or evidence.page_number is None,
                source_url=evidence.source_url if evidence else None,
                source_reference=evidence.source_reference if evidence else None,
                quote=c.snippet,
                verified=c.verified,
                verification_status=evidence.verification_status if evidence else "unverified_secondary",
                provenance_warning=evidence.provenance_warning if evidence else None,
            )
        )

    evidence_items = [
        EvidenceItem(
            chunk_id=e.chunk_id or "",
            document_id=e.document_id or "",
            document_title=e.document_title,
            page_number=e.page_number,
            page_information_unavailable=e.page_number is None,
            section=e.section,
            snippet=e.text[:900],
            score=round(e.score, 6),
            source_url=e.source_url,
            source_reference=e.source_reference,
            source_name=e.source_name,
            source_tier=e.source_tier,
            document_type=e.document_type,
            year=None,
            language=e.language,
            retrieval=(
                f"{e.document_title}"
                + (f", page {e.page_number}" if e.page_number is not None else "")
                + (f", {e.source_name}" if e.source_name else "")
            ),
        )
        for e in result.evidence
    ]

    refused = result.answer_kind == AnswerKind.INSUFFICIENT_EVIDENCE
    return RagResponse(
        refusal_code=result.refusal_reason,
        refusal_reason=_REFUSAL_REASONS.get(result.refusal_reason or ""),
        query_id=result.query_id,
        answer=result.answer,
        answer_kind=result.answer_kind,
        answer_language=result.language,
        refused=refused,
        groundedness=round(result.groundedness, 4),
        evidence=evidence_items,
        citations=citations,
        claims=[
            {"text": c.text, "kind": c.kind, "markers": c.markers, "supported": c.supported}
            for c in result.claims
        ],
        conflicting_evidence=result.conflicting,
        conflict_note=result.conflict_note,
        providers={
            "llm": result.llm_provider,
            "model": result.llm_model,
            "retrieval": "hybrid (full text + vector + entity)",
        },
        duration_ms=result.latency_ms,
        disclaimer=NO_ANSWER_DISCLAIMER,
    )


@router.post("/rag/ask", response_model=RagResponse, summary="Ask the archive")
def ask(payload: RagRequest, db: DbSession, principal: CurrentPrincipal) -> RagResponse:
    published = db.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.deleted_at.is_(None),
            Document.publication_status == PublicationStatus.PUBLISHED,
        )
    ) or 0
    if published == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The archive has no published, indexed sources yet, so it cannot answer "
                "questions. This is a deliberate refusal, not a failure."
            ),
        )

    result = answer_question(
        db, _service_request(payload, principal), persist=payload.persist
    )
    if payload.persist and result.query_id:
        # Anonymous kiosk visitors are tracked by a device-scoped session key.
        db.merge(
            UserResearchItem(
                id=f"{result.query_id[:32]}-rq",
                user_id=principal.id,
                session_key=principal.session_key[:64],
                kind="rag_query",
                target_type="rag_query",
                target_id=result.query_id,
                title=payload.question[:400],
                snippet=result.answer[:2000],
            )
        )
        db.commit()
    return _response(result, payload)


@router.get("/rag/history", response_model=list[RagHistoryItem], summary="Your recent questions")
def history(
    db: DbSession,
    principal: CurrentPrincipal,
    limit: int = Query(20, ge=1, le=100),
) -> list[RagHistoryItem]:
    if principal.is_authenticated:
        queries = db.scalars(
            select(RagQuery)
            .where(RagQuery.user_id == principal.id)
            .order_by(RagQuery.created_at.desc())
            .limit(limit)
        ).all()
    else:
        ids = db.scalars(
            select(UserResearchItem.target_id).where(
                UserResearchItem.kind == "rag_query",
                UserResearchItem.session_key == principal.session_key[:64],
            )
        ).all()
        if not ids:
            return []
        queries = db.scalars(
            select(RagQuery)
            .where(RagQuery.id.in_(ids))
            .order_by(RagQuery.created_at.desc())
            .limit(limit)
        ).all()

    out: list[RagHistoryItem] = []
    for q in queries:
        citations = db.scalar(
            select(func.count()).select_from(Citation).where(Citation.query_id == q.id)
        ) or 0
        out.append(
            RagHistoryItem(
                id=q.id,
                question=q.question,
                answer=q.answer,
                answer_kind=q.answer_kind,
                refused=q.answer_kind == AnswerKind.INSUFFICIENT_EVIDENCE,
                groundedness=round(float(q.groundedness or 0.0), 4),
                created_at=q.created_at,
                citation_count=int(citations),
                evidence_count=len((q.retrieval or {}).get("evidence", [])),
            )
        )
    return out


@router.get("/rag/history/{query_id}", summary="A past answer with its citations")
def history_item(
    query_id: str, db: DbSession, principal: CurrentPrincipal
) -> dict[str, Any]:
    query = db.get(RagQuery, query_id)
    if query is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That research item was not found."
        )
    owned = query.user_id == principal.id
    in_session = db.scalar(
        select(func.count())
        .select_from(UserResearchItem)
        .where(
            UserResearchItem.kind == "rag_query",
            UserResearchItem.target_id == query_id,
            UserResearchItem.session_key == principal.session_key[:64],
        )
    )
    if not (owned or in_session or principal.is_authenticated):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="That research item belongs to another session."
        )

    citations = db.scalars(
        select(Citation).where(Citation.query_id == query_id).order_by(Citation.rank)
    ).all()
    evidence = (query.retrieval or {}).get("evidence", [])
    return {
        "id": query.id,
        "question": query.question,
        "answer": query.answer,
        "answer_kind": query.answer_kind,
        "groundedness": query.groundedness,
        "conflicting": (query.retrieval or {}).get("conflicting"),
        "created_at": query.created_at,
        "evidence": evidence,
        "citations": [
            {
                "marker": c.rank,
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "page_id": c.page_id,
                "quote": c.snippet,
                "verified": c.verified,
                "validation_note": c.validation_note,
            }
            for c in citations
        ],
    }


@router.get("/rag/research-trail", response_model=list[ResearchItemOut], summary="Saved research")
def research_trail(
    db: DbSession, principal: CurrentPrincipal, limit: int = Query(20, ge=1, le=50)
) -> list[ResearchItemOut]:
    if not principal.is_authenticated:
        return []
    rows = db.scalars(
        select(UserResearchItem)
        .where(
            UserResearchItem.user_id == principal.id,
            UserResearchItem.kind == "rag_query",
        )
        .order_by(UserResearchItem.created_at.desc())
        .limit(limit)
    ).all()
    out: list[ResearchItemOut] = []
    for item in rows:
        query = db.get(RagQuery, item.target_id)
        if query is None:
            continue
        citations = db.scalars(
            select(Citation).where(Citation.query_id == query.id).order_by(Citation.rank)
        ).all()
        evidence = (query.retrieval or {}).get("evidence", [])
        out.append(
            ResearchItemOut(
                id=item.id,
                question=query.question,
                answer=query.answer,
                answer_kind=query.answer_kind,
                groundedness=round(float(query.groundedness or 0.0), 4),
                citations=[
                    CitationItem(
                        marker=c.rank,
                        chunk_id=c.chunk_id or "",
                        document_id=c.document_id or "",
                        document_title=(
                            next(
                                (
                                    e.get("title", "")
                                    for e in evidence
                                    if e.get("chunk_id") == c.chunk_id
                                ),
                                "",
                            )
                        ),
                        page_number=_trail_page(evidence, c.chunk_id),
                        page_information_unavailable=_trail_page(evidence, c.chunk_id) is None,
                        quote=c.snippet,
                        verified=c.verified,
                        verification_status=_trail_field(
                            evidence, c.chunk_id, "verification_status"
                        )
                        or "unverified_secondary",
                        provenance_warning=_trail_field(evidence, c.chunk_id, "provenance_warning"),
                    )
                    for c in citations
                ],
                created_at=item.created_at,
            )
        )
    return out


@router.get("/rag/limits", summary="Current RAG honesty thresholds")
def limits() -> dict[str, Any]:
    """Exposed so the UI can state the same limits the engine enforces."""
    from app.core.config import settings

    return {
        "evidence_threshold": settings.rag_evidence_threshold,
        "min_evidence": settings.rag_min_evidence,
        "max_evidence": settings.rag_max_evidence,
        "groundedness_threshold": settings.rag_groundedness_threshold,
        "page_fallback_text": UNAVAILABLE_PAGE,
        "answer_kinds": [k.value for k in AnswerKind],
    }
