"""Hybrid retrieval.

Three independent retrievers run against PostgreSQL and their ranks are fused
with Reciprocal Rank Fusion, then reranked:

1. **Keyword** — ``ts_rank_cd`` over a GIN-indexed ``tsvector`` built with the
   ``simple`` configuration so Devanagari is handled without stemming artefacts.
2. **Vector** — cosine distance over ``pgvector`` using the configured embedding
   provider.
3. **Metadata/entity** — exact and trigram matches on titles, persons, topics,
   constitutional articles and source names.

The raw signals are returned as retrieval diagnostics. They are deliberately
*not* surfaced to visitors as a "confidence score".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from datetime import date

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.archive import Document, DocumentChunk
from app.models.knowledge import DocumentPerson, TopicLink
from app.models.enums import PublicationStatus
from app.providers.embedding import _tokens, get_embedding_provider
from app.providers.rerank import get_reranker_provider

log = get_logger(__name__)

SearchMode = Literal["hybrid", "keyword", "semantic", "entity"]
KRRF = settings.rag_rrf_k


@dataclass(slots=True)
class SearchFilters:
    languages: list[str] = field(default_factory=list)
    document_types: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    collection_ids: list[str] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    person_ids: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    publication_statuses: list[str] = field(
        default_factory=lambda: [PublicationStatus.PUBLISHED.value]
    )
    is_demo: bool | None = None
    include_drafts: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "languages": self.languages,
            "document_types": self.document_types,
            "document_ids": self.document_ids,
            "collection_ids": self.collection_ids,
            "topic_ids": self.topic_ids,
            "source_ids": self.source_ids,
            "person_ids": self.person_ids,
            "year_from": self.year_from,
            "year_to": self.year_to,
            "date_from": self.date_from,
            "date_to": self.date_to,
        }


@dataclass(slots=True)
class Passage:
    chunk_id: str
    document_id: str
    text: str
    score: float
    vector_score: float = 0.0
    keyword_score: float = 0.0
    entity_score: float = 0.0
    rerank_score: float = 0.0
    rank_vector: int | None = None
    rank_keyword: int | None = None
    rank_entity: int | None = None
    rrf_score: float = 0.0
    page_number: int | None = None
    section: str | None = None
    language: str = "en"
    source_url: str | None = None
    source_reference: str[str] | None = None

    def as_diagnostics(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "rrf": round(self.rrf_score, 6),
            "vector": round(self.vector_score, 6),
            "keyword": round(self.keyword_score, 6),
            "entity": round(self.entity_score, 6),
            "rerank": round(self.rerank_score, 6),
            "ranks": {
                "vector": self.rank_vector,
                "keyword": self.rank_keyword,
                "entity": self.rank_entity,
            },
        }


@dataclass(slots=True)
class SearchHit:
    passage: Passage
    document: Document


def _fts_query(query: str) -> str:
    """Build a websearch-style tsquery that is safe for any user input."""
    terms = [t for t in re.findall(r"[\w\u0900-\u097F]+", query, re.UNICODE) if len(t) > 1]
    if not terms:
        return ""
    quoted = " | ".join(f"'{t}'" for t in terms[:24])
    return f"({quoted})"


def _apply_filters(stmt: Select, filters: SearchFilters) -> Select:
    doc = Document
    statuses = list(filters.publication_statuses)
    if filters.include_drafts:
        statuses = list(
            set(statuses)
            | {PublicationStatus.DRAFT.value, PublicationStatus.IN_REVIEW.value}
        )
    if statuses:
        stmt = stmt.where(doc.publication_status.in_(statuses))
    stmt = stmt.where(doc.deleted_at.is_(None))
    if filters.languages:
        stmt = stmt.where(doc.language.in_(filters.languages))
    if filters.document_types:
        stmt = stmt.where(doc.document_type.in_(filters.document_types))
    if filters.document_ids:
        stmt = stmt.where(doc.id.in_(filters.document_ids))
    if filters.collection_ids:
        stmt = stmt.where(doc.collection_id.in_(filters.collection_ids))
    if filters.source_ids:
        stmt = stmt.where(doc.source_id.in_(filters.source_ids))
    if filters.topic_ids:
        # EXISTS rather than a join: topic_documents is a link table and joining
        # it would multiply rows and corrupt the RRF ranking.
        stmt = stmt.where(
            exists().where(
                and_(
                    TopicLink.document_id == doc.id,
                    TopicLink.topic_id.in_(filters.topic_ids),
                )
            )
        )
    if filters.person_ids:
        stmt = stmt.where(
            exists().where(
                and_(
                    DocumentPerson.document_id == doc.id,
                    DocumentPerson.person_id.in_(filters.person_ids),
                )
            )
        )
    if filters.date_from:
        stmt = stmt.where(doc.document_date >= date.fromisoformat(filters.date_from))
    if filters.date_to:
        stmt = stmt.where(doc.document_date <= date.fromisoformat(filters.date_to))
    if filters.year_from is not None:
        stmt = stmt.where(doc.year.isnot(None), doc.year >= filters.year_from)
    if filters.year_to is not None:
        stmt = stmt.where(doc.year.isnot(None), doc.year <= filters.year_to)
    if filters.is_demo is not None:
        stmt = stmt.where(doc.is_demo.is_(filters.is_demo))
    return stmt


def _keyword_passages(
    db: Session, query: str, filters: SearchFilters, limit: int
) -> list[Passage]:
    tsquery = _fts_query(query)
    if not tsquery:
        return []
    rank = func.ts_rank_cd(DocumentChunk.search_tsv, func.websearch_to_tsquery(tsquery))
    stmt = (
        select(DocumentChunk, Document, rank.label("kw"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.search_tsv.isnot(None))
        .where(func.websearch_to_tsquery(tsquery).op("@@")(DocumentChunk.search_tsv))
    )
    stmt = _apply_filters(stmt, filters).order_by(rank.desc()).limit(limit)
    rows = db.execute(stmt).all()
    out: list[Passage] = []
    for i, (chunk, doc, kw) in enumerate(rows):
        out.append(
            Passage(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                text=chunk.text,
                score=float(kw or 0.0),
                keyword_score=float(kw or 0.0),
                rank_keyword=i + 1,
                page_number=chunk.page_number,
                section=chunk.section,
                language=chunk.language,
                source_url=chunk.source_url,
                source_reference=chunk.source_reference,
            )
        )
    return out


def _vector_passages(
    db: Session, query: str, filters: SearchFilters, limit: int
) -> list[Passage]:
    provider = get_embedding_provider()
    try:
        vector = provider.embed_query(query)
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding failed; vector leg skipped", error=str(exc))
        return []
    if not vector:
        return []
    distance = DocumentChunk.embedding.cosine_distance(vector)
    stmt = (
        select(DocumentChunk, Document, distance.label("dist"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.embedding.isnot(None))
    )
    stmt = _apply_filters(stmt, filters).order_by(distance.asc()).limit(limit)
    rows = db.execute(stmt).all()
    out: list[Passage] = []
    for i, (chunk, doc, dist) in enumerate(rows):
        similarity = max(0.0, 1.0 - float(dist))
        out.append(
            Passage(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                text=chunk.text,
                score=similarity,
                vector_score=similarity,
                rank_vector=i + 1,
                page_number=chunk.page_number,
                section=chunk.section,
                language=chunk.language,
                source_url=chunk.source_url,
                source_reference=chunk.source_reference,
            )
        )
    return out


def _entity_passages(
    db: Session, query: str, filters: SearchFilters, limit: int
) -> list[Passage]:
    """Title / speaker / person / topic matches.

    Runs even when the FTS leg finds nothing so that queries naming a person or a
    constitutional article still return the right record.
    """
    tokens = [t for t in _tokens(query) if len(t) > 2]
    if not tokens:
        return []
    doc = Document
    clauses = []
    for token in tokens[:8]:
        pattern = f"%{token}%"
        clauses.append(doc.title.ilike(pattern))
        clauses.append(doc.subtitle.ilike(pattern))
        clauses.append(doc.speaker.ilike(pattern))
        clauses.append(doc.author_display.ilike(pattern))
        clauses.append(doc.source_reference.ilike(pattern))
    match = or_(*clauses)
    stmt = (
        select(DocumentChunk, Document, func.length(DocumentChunk.text).label("len"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(match)
    )
    stmt = _apply_filters(stmt, filters).order_by(Document.year.desc().nullslast()).limit(limit)
    rows = db.execute(stmt).all()
    out: list[Passage] = []
    for i, (chunk, d, _len) in enumerate(rows):
        # Count how many query tokens the document title actually covers.
        title_tokens = set(_tokens(d.title or "")) | set(_tokens(d.speaker or ""))
        hits = sum(1 for t in tokens if t in title_tokens)
        out.append(
            Passage(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                text=chunk.text,
                score=hits / max(1, len(tokens)),
                entity_score=hits / max(1, len(tokens)),
                rank_entity=i + 1,
                page_number=chunk.page_number,
                section=chunk.section,
                language=chunk.language,
                source_url=chunk.source_url,
                source_reference=chunk.source_reference,
            )
        )
    return out


def _rrf_fuse(
    legs: dict[str, list[Passage]], weights: dict[str, float]
) -> dict[str, Passage]:
    fused: dict[str, Passage] = {}
    for leg_name, passages in legs.items():
        weight = weights.get(leg_name, 1.0)
        for rank, passage in enumerate(passages, start=1):
            contribution = weight / (KRRF + rank)
            existing = fused.get(passage.chunk_id)
            if existing is None:
                passage.rrf_score = contribution
                passage.score = contribution
                fused[passage.chunk_id] = passage
            else:
                existing.rrf_score += contribution
                existing.score = existing.rrf_score
                for attr in ("vector_score", "keyword_score", "entity_score",
                             "rank_vector", "rank_keyword", "rank_entity"):
                    new_value = getattr(passage, attr)
                    if new_value:
                        setattr(existing, attr, new_value)
                if not existing.page_number and passage.page_number:
                    existing.page_number = passage.page_number
                if not existing.source_url and passage.source_url:
                    existing.source_url = passage.source_url
                if not existing.section and passage.section:
                    existing.section = passage.section
    return fused


def _rerank(
    query: str, passages: list[Passage], top_k: int, tiers: list[str] | None = None
) -> list[Passage]:
    provider = get_reranker_provider()
    try:
        items = provider.rerank(  # type: ignore[call-arg]
            query=query,
            documents=[p.text for p in passages],
            top_k=top_k,
            base_scores=[p.rrf_score for p in passages],
            source_tiers=tiers or ["unknown"] * len(passages),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("reranker failed; falling back to fusion order", error=str(exc))
        return sorted(passages, key=lambda p: p.rrf_score, reverse=True)[:top_k]
    ordered: list[Passage] = []
    for rank, item in enumerate(items, start=1):
        if item.index < 0 or item.index >= len(passages):
            continue
        passage = passages[item.index]
        passage.rerank_score = item.score
        passage.score = item.score
        ordered.append(passage)
    return ordered[:top_k]


def hybrid_search(
    db: Session,
    query: str,
    *,
    filters: SearchFilters | None = None,
    mode: SearchMode = "hybrid",
    limit: int | None = None,
    pool: int | None = None,
    rerank: bool = True,
) -> tuple[list[Passage], dict[str, Any]]:
    filters = filters or SearchFilters()
    limit = limit or settings.search_default_limit
    pool_size = pool or settings.rag_candidate_pool
    query = (query or "").strip()
    if not query:
        return [], {"reason": "empty_query", "legs": {}}

    legs: dict[str, list[Passage]] = {}
    weights: dict[str, float] = {}
    if mode in ("hybrid", "keyword"):
        legs["keyword"] = _keyword_passages(
            db, query, filters, settings.search_keyword_candidates
        )
        weights["keyword"] = settings.rag_keyword_weight
    if mode in ("hybrid", "semantic"):
        legs["vector"] = _vector_passages(db, query, filters, settings.search_vector_candidates)
        weights["vector"] = settings.rag_vector_weight
    if mode in ("hybrid", "entity"):
        legs["entity"] = _entity_passages(db, query, filters, 40)
        weights["entity"] = 0.6

    fused = _rrf_fuse(legs, weights)
    candidates = list(fused.values())
    candidates.sort(key=lambda p: p.rrf_score, reverse=True)
    candidates = candidates[:pool_size]

    doc_ids = {p.document_id for p in candidates}
    tiers: list[str] = []
    if candidates and doc_ids:
        from app.models.archive import Source

        rows = db.execute(
            select(Document.id, Source.tier).outerjoin(Source, Source.id == Document.source_id).where(
                Document.id.in_(doc_ids)
            )
        ).all()
        tier_map = {r[0]: (r[1] or "unknown") for r in rows}
        tiers = [tier_map.get(p.document_id, "unknown") for p in candidates]

    if rerank and candidates:
        ranked = _rerank(query, candidates, top_k=max(limit * 3, limit), tiers=tiers)
    else:
        ranked = candidates[: max(limit * 3, limit)]

    diagnostics = {
        "mode": mode,
        "legs": {name: len(items) for name, items in legs.items()},
        "candidates": len(candidates),
        "returned": min(limit, len(ranked)),
        "reranker": get_reranker_provider().name,
        "embedding": get_embedding_provider().name,
        "top": [p.as_diagnostics() for p in ranked[:5]],
    }
    return ranked[: limit * 3], diagnostics


def highlight(text: str, query: str, *, max_terms: int = 12) -> list[tuple[str, bool]]:
    """Return (fragment, is_match) pairs for the UI to highlight.

    Picks the densest window of query-term matches instead of always returning
    the first 300 characters, so results lead with the relevant passage.
    """
    terms = sorted({t for t in re.findall(r"[\w\u0900-\u097F]+", query, re.UNICODE) if len(t) > 1})
    if not terms:
        return [(text[:600] + ("…" if len(text) > 600 else ""), False)]
    pattern = re.compile("|".join(re.escape(t) for t in terms[:max_terms]), re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [(text[:600] + ("…" if len(text) > 600 else ""), False)]

    window = 460
    best_start, best_hits = 0, 0
    for m in matches:
        start = max(0, m.start() - window // 3)
        end = start + window
        hits = sum(1 for x in matches if start <= x.start() < end)
        if hits > best_hits:
            best_hits, best_start = hits, start
    end = min(len(text), best_start + window)
    fragment = text[best_start:end]
    if best_start > 0:
        fragment = "…" + fragment
    if end < len(text):
        fragment = fragment + "…"

    out: list[tuple[str, bool]] = []
    cursor = 0
    for part in pattern.split(fragment):
        if not part:
            continue
        is_match = pattern.fullmatch(part) is not None
        if out and out[-1][1] == is_match:
            out[-1] = (out[-1][0] + part, is_match)
        else:
            out.append((part, is_match))
    return out


def search_suggestions(db: Session, query: str, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    """Type-ahead across documents, people, events, topics and articles."""
    query = (query or "").strip()
    if len(query) < 2:
        return {"documents": [], "people": [], "events": [], "topics": [], "articles": []}
    pattern = f"%{query}%"
    from app.models.knowledge import ConstitutionalArticle, Event, Person, Topic

    documents = db.execute(
        select(Document.id, Document.title, Document.document_type, Document.year)
        .where(Document.title.ilike(pattern), Document.deleted_at.is_(None))
        .order_by(Document.year.asc().nullslast())
        .limit(limit)
    ).all()
    people = db.execute(
        select(Person.id, Person.canonical_name, Person.birth_year, Person.death_year)
        .where(Person.canonical_name.ilike(pattern))
        .limit(limit)
    ).all()
    events = db.execute(
        select(Event.id, Event.title, Event.year, Event.date_label)
        .where(Event.title.ilike(pattern))
        .limit(limit)
    ).all()
    topics = db.execute(
        select(Topic.id, Topic.name, Topic.category)
        .where(Topic.name.ilike(pattern))
        .limit(limit)
    ).all()
    articles = db.execute(
        select(
            ConstitutionalArticle.id,
            ConstitutionalArticle.article_number,
            ConstitutionalArticle.heading,
        ).where(
            or_(
                ConstitutionalArticle.article_number.ilike(pattern),
                ConstitutionalArticle.heading.ilike(pattern),
            )
        ).limit(limit)
    ).all()
    return {
        "documents": [
            {"id": d[0], "title": d[1], "document_type": d[2], "year": d[3]} for d in documents
        ],
        "people": [{"id": p[0], "name": p[1], "born": p[2], "died": p[3]} for p in people],
        "events": [
            {"id": e[0], "title": e[1], "year": e[2], "date": e[3]} for e in events
        ],
        "topics": [{"id": t[0], "name": t[1], "category": t[2]} for t in topics],
        "articles": [
            {"id": a[0], "number": a[1], "heading": a[2]} for a in articles
        ],
    }
