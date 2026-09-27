"""Search endpoint.

Search is hybrid by default: PostgreSQL full text, pgvector similarity and
entity matches are fused with reciprocal rank fusion and reranked. Every hit
carries the pointer needed to verify it — document, page and original source — or
an explicit note that the indexed source has no page information.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentPrincipal, DbSession
from app.core.security import has_permission
from app.models.archive import Document
from app.models.enums import PublicationStatus
from app.schemas import SearchRequest, SearchResponse, SearchResultItem, SearchSnippet
from app.services.search import SearchFilters, highlight, hybrid_search, search_suggestions

router = APIRouter(tags=["search"])


def _filters(payload: SearchRequest, principal) -> SearchFilters:
    include_drafts = payload.include_drafts and principal.is_authenticated
    return SearchFilters(
        languages=payload.languages,
        document_types=payload.document_types,
        collection_ids=payload.collection_ids,
        topic_ids=payload.topic_ids,
        person_ids=payload.person_ids,
        source_ids=payload.source_ids,
        year_from=payload.year_from,
        year_to=payload.year_to,
        date_from=payload.date_from.isoformat() if payload.date_from else None,
        date_to=payload.date_to.isoformat() if payload.date_to else None,
        include_drafts=include_drafts,
    )


@router.post("/search", response_model=SearchResponse, summary="Hybrid archive search")
def search(payload: SearchRequest, db: DbSession, principal: CurrentPrincipal) -> SearchResponse:
    passages, diagnostics = hybrid_search(
        db,
        payload.query,
        filters=_filters(payload, principal),
        mode=payload.mode,
        limit=payload.limit,
        rerank=payload.rerank,
    )
    # hybrid_search returns a wider pool so the client can page through it.
    selected = passages[: payload.limit]

    documents = {
        d.id: d
        for d in db.scalars(
            select(Document).where(Document.id.in_({p.document_id for p in selected}))
        ).all()
    } if selected else {}
    from app.models.archive import DocumentChunk

    chunk_rows = {
        c.id: c
        for c in db.scalars(
            select(DocumentChunk).where(DocumentChunk.id.in_({p.chunk_id for p in selected}))
        ).all()
    } if selected else {}
    source_ids = {d.source_id for d in documents.values() if d.source_id}
    from app.models.archive import Source

    sources = {
        s.id: s
        for s in db.scalars(select(Source).where(Source.id.in_(source_ids))).all()
    } if source_ids else {}

    results: list[SearchResultItem] = []
    for p in selected:
        doc = documents.get(p.document_id)
        if doc is None:
            continue
        source = sources.get(doc.source_id) if doc.source_id else None
        results.append(
            SearchResultItem(
                chunk_id=p.chunk_id,
                document_id=p.document_id,
                document_title=doc.title,
                slug=doc.slug,
                score=round(p.score, 6),
                page_number=p.page_number,
                page_information_unavailable=p.page_number is None,
                section=p.section,
                language=p.language,
                snippet=SearchSnippet(
                    text=p.text[:1200],
                    highlighted=[
                        {"text": text, "match": is_match}
                        for text, is_match in highlight(p.text, payload.query)
                    ],
                ),
                source_url=source.source_url if source else p.source_url,
                source_reference=p.source_reference,
                source_name=source.name if source else None,
                source_tier=source.tier if source else None,
                document_type=doc.document_type,
                year=doc.year,
                verification_status=str(doc.verification_status),
                quote_verified=bool(
                    chunk_rows[p.chunk_id].quote_verified
                    if p.chunk_id in chunk_rows
                    else False
                ),
                provenance_warning=(
                    None
                    if (
                        str(doc.verification_status) == "verified_primary"
                        and chunk_rows.get(p.chunk_id)
                        and chunk_rows[p.chunk_id].quote_verified
                    )
                    else "Unverified secondary text. This is a third-party summary of the "
                    "cited source, not a checked transcription."
                ),
                diagnostics=p.as_diagnostics() if payload.explain else None,
            )
        )

    suggestions = None
    if not results:
        suggestions = search_suggestions(db, payload.query)

    return SearchResponse(
        query=payload.query,
        mode=payload.mode,
        total=len(results),
        results=results,
        diagnostics=diagnostics if payload.explain else {"candidates": diagnostics.get("candidates", 0)},
        suggestions=suggestions,
        notices=(
            []
            if results
            else [
                "No published source matched. The archive does not invent results for "
                "material it does not hold."
            ]
        ),
    )


@router.get("/search/suggest", summary="Search suggestions")
def suggest(
    q: str, db: DbSession, _: CurrentPrincipal
) -> dict[str, list[dict[str, Any]]]:
    if len(q.strip()) < 2:
        return {"documents": [], "people": [], "topics": [], "events": []}
    return search_suggestions(db, q)


@router.get("/search/facets", summary="Documents grouped by type and language")
def search_facets(db: DbSession, _: CurrentPrincipal) -> dict[str, Any]:
    total = (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.deleted_at.is_(None),
                Document.publication_status == PublicationStatus.PUBLISHED,
            )
        )
        or 0
    )
    if total == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The archive has no published documents yet.",
        )
    by_type = db.execute(
        select(Document.document_type, func.count())
        .where(
            Document.deleted_at.is_(None),
            Document.publication_status == PublicationStatus.PUBLISHED,
        )
        .group_by(Document.document_type)
        .order_by(func.count().desc())
    ).all()
    by_language = db.execute(
        select(Document.language, func.count())
        .where(
            Document.deleted_at.is_(None),
            Document.publication_status == PublicationStatus.PUBLISHED,
        )
        .group_by(Document.language)
        .order_by(func.count().desc())
    ).all()
    return {
        "total": total,
        "document_types": [
            {"value": t, "count": int(n), "label": str(t).replace("_", " ").title()}
            for t, n in by_type
        ],
        "languages": [{"value": lang, "count": int(n)} for lang, n in by_language],
    }
