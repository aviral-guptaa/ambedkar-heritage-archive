"""Knowledge graph, timeline and taxonomy endpoints.

The graph is served from Neo4j when connected and from the relational mirror
otherwise; every response states which backend answered. Traversal is capped and
truncation is reported, never hidden. Extraction confidence is carried through to
the UI so an unreviewed relationship is visibly different from a reviewed one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentPrincipal, DbSession
from app.models.archive import Document
from app.models.enums import PublicationStatus
from app.models.knowledge import (
    ConstitutionalArticle,
    DocumentPerson,
    Event,
    EventDocument,
    GraphEdge,
    GraphNode,
    Person,
    Topic,
    TopicLink,
)
from app.providers.base import GraphNodeData
from app.providers.graph import get_graph_store, graph_capability
from app.schemas import (
    EventOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphResponse,
    GraphSearchResponse,
    GraphStatsResponse,
    PersonOut,
    TimelineResponse,
    TopicOut,
)

router = APIRouter(tags=["graph"])

#: An edge is treated as reviewed when it carries no pending-review marker.
#: Extraction writes `review_status: pending`; an archivist's decision replaces it.
PENDING_KEYS = ("pending", "unreviewed", "auto")


def _edge_verified(edge: GraphEdge) -> bool:
    props = edge.properties or {}
    status = str(props.get("review_status", props.get("status", ""))).lower()
    return status not in PENDING_KEYS


def _node_out(node: GraphNodeData) -> GraphNodeOut:
    props = node.properties or {}
    return GraphNodeOut(
        id=node.id,
        type=str(node.entity_type),
        label=node.label,
        properties={**props, "name": node.name, "year": node.year, "summary": node.summary},
        degree=node.degree,
        verified=str(props.get("review_status", "verified")).lower() not in PENDING_KEYS,
    )


def _edge_out(edge: GraphEdge) -> GraphEdgeOut:
    return GraphEdgeOut(
        id=edge.id,
        source=edge.src_id,
        target=edge.dst_id,
        type=str(edge.relation),
        properties={
            **(edge.properties or {}),
            "weight": edge.weight,
            "confidence": edge.confidence,
            "relationship_id": edge.relationship_id,
        },
        verified=_edge_verified(edge),
        provenance=(edge.properties or {}).get("source_url"),
    )


def _published_ids() -> Any:
    return (
        select(Document.id)
        .where(Document.deleted_at.is_(None), Document.publication_status == PublicationStatus.PUBLISHED)
        .subquery()
    )


@router.get("/graph", response_model=GraphResponse, summary="Graph neighbourhood or overview")
def graph(
    db: DbSession,
    _: CurrentPrincipal,
    node_id: str | None = Query(default=None, description="Centre node; omit for the overview"),
    depth: int = Query(1, ge=1, le=3),
    limit: int = Query(150, ge=10, le=600),
    entity_types: list[str] = Query(default=[]),
) -> GraphResponse:
    store = get_graph_store()
    capability = graph_capability()
    backend = str(capability.get("backend") or store.name)
    notices: list[str] = []

    if not capability.get("available"):
        return GraphResponse(
            nodes=[],
            edges=[],
            center=node_id,
            depth=depth,
            backend=backend,
            notices=[
                "The knowledge graph is empty. Relationships are extracted from sources "
                "during ingestion and reviewed before they are presented as fact."
            ],
        )

    if node_id:
        subgraph = store.neighborhood(
            node_id,
            depth=depth,
            limit=limit,
            entity_types=entity_types or None,
        )
        nodes = subgraph.nodes
        edges = subgraph.edges
        truncated = subgraph.truncated
    else:
        # Overview: the most connected entities, so the view is never empty just
        # because the visitor has not clicked anything yet.
        rows = db.execute(
            select(GraphNode, func.count(GraphEdge.id))
            .outerjoin(GraphEdge, GraphEdge.src_id == GraphNode.id)
            .group_by(GraphNode.id)
            .order_by(func.count(GraphEdge.id).desc())
            .limit(limit)
        ).all()
        nodes = [
            GraphNodeData(
                id=n.id,
                label=n.label,
                entity_type=str(n.entity_type),
                name=n.name,
                year=n.year,
                summary=n.summary,
                properties=n.properties or {},
                degree=int(degree),
            )
            for n, degree in rows
        ]
        ids = [n.id for n in nodes]
        edge_rows = (
            db.scalars(select(GraphEdge).where(GraphEdge.src_id.in_(ids))).all() if ids else []
        )
        from app.providers.base import GraphEdgeData

        edges = [
            GraphEdgeData(
                id=e.id,
                source=e.src_id,
                target=e.dst_id,
                relation=str(e.relation),
                weight=e.weight,
                confidence=e.confidence,
                properties=e.properties or {},
            )
            for e in edge_rows
        ]
        truncated = False
        notices.append(
            "Showing the most connected entities. Select a node to traverse its neighbourhood."
        )

    if entity_types:
        nodes = [n for n in nodes if str(n.entity_type) in entity_types]
    kept = {n.id for n in nodes}
    node_objs = [_node_out(n) for n in nodes]
    edge_objs = [
        GraphEdgeOut(
            id=e.id,
            source=e.source,
            target=e.target,
            type=e.relation,
            properties={**(e.properties or {}), "weight": e.weight, "confidence": e.confidence},
            verified=str((e.properties or {}).get("review_status", "verified")).lower()
            not in PENDING_KEYS,
            provenance=(e.properties or {}).get("source_url"),
        )
        for e in edges
        if e.source in kept and e.target in kept
    ]

    if truncated:
        notices.append(f"Traversal was capped at {limit} nodes. Narrow the query or pick a node.")
    if backend != "neo4j":
        notices.append("Served from the relational graph mirror; Neo4j is not connected.")
    pending = sum(1 for e in edge_objs if not e.verified)
    if pending:
        notices.append(
            f"{pending} of {len(edge_objs)} relationships are awaiting human review and are "
            "marked as unverified in the interface."
        )

    return GraphResponse(
        nodes=node_objs,
        edges=edge_objs,
        center=node_id,
        depth=depth,
        backend=backend,
        truncated=truncated,
        notices=notices,
    )


@router.get("/graph/stats", response_model=GraphStatsResponse, summary="Graph size and review state")
def graph_stats(db: DbSession, _: CurrentPrincipal) -> GraphStatsResponse:
    capability = graph_capability()
    nodes = db.scalar(select(func.count()).select_from(GraphNode)) or 0
    edges = db.scalar(select(func.count()).select_from(GraphEdge)) or 0
    pending = (
        db.scalar(
            select(func.count())
            .select_from(GraphEdge)
            .where(GraphEdge.properties["review_status"].as_string().in_(list(PENDING_KEYS)))
        )
        or 0
    )
    by_type = dict(
        db.execute(
            select(GraphNode.entity_type, func.count()).group_by(GraphNode.entity_type)
        ).all()
    )
    return GraphStatsResponse(
        backend=str(capability.get("backend") or "postgres"),
        nodes=int(nodes),
        edges=int(edges),
        verified_edges=int(edges) - int(pending),
        pending_edges=int(pending),
        by_type={str(k): int(v) for k, v in by_type.items()},
    )


@router.get("/graph/search", response_model=GraphSearchResponse, summary="Find graph nodes")
def graph_search(
    q: str, db: DbSession, _: CurrentPrincipal, limit: int = Query(20, ge=1, le=100)
) -> GraphSearchResponse:
    store = get_graph_store()
    backend = str(graph_capability().get("backend") or store.name)
    if not store.is_available():
        return GraphSearchResponse(query=q, nodes=[], backend=backend)
    return GraphSearchResponse(
        query=q,
        nodes=[_node_out(n) for n in store.search_nodes(q, limit=limit)],
        backend=backend,
    )


@router.get("/graph/path", response_model=GraphResponse, summary="Shortest verified path")
def graph_path(
    _: CurrentPrincipal,
    source: str = Query(..., min_length=1),
    target: str = Query(..., min_length=1),
    max_depth: int = Query(4, ge=1, le=6),
) -> GraphResponse:
    store = get_graph_store()
    backend = str(graph_capability().get("backend") or store.name)
    if not store.is_available():
        return GraphResponse(nodes=[], edges=[], backend=backend)
    try:
        subgraph = store.path_between(source, target, max_depth)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No verified path was found between those entities: {exc}",
        ) from exc
    notices = []
    if not subgraph.nodes:
        notices.append(
            "The archive holds no verified relationship connecting those two entities yet."
        )
    return GraphResponse(
        nodes=[_node_out(n) for n in subgraph.nodes],
        edges=[
            GraphEdgeOut(
                id=e.id,
                source=e.source,
                target=e.target,
                type=e.relation,
                properties={**(e.properties or {}), "weight": e.weight, "confidence": e.confidence},
            )
            for e in subgraph.edges
        ],
        backend=subgraph.backend or backend,
        truncated=subgraph.truncated,
        notices=notices,
    )


# --------------------------------------------------------------------------- #
# timeline
# --------------------------------------------------------------------------- #

#: Lower rank means less trustworthy. Used to pick the weakest status in a group.
_STATUS_RANK = {
    "verified_primary": 0,
    "pending_review": 1,
    "machine_translation": 2,
    "unverified_secondary": 3,
}


@router.get("/timeline", response_model=TimelineResponse, summary="Timeline events")
def timeline(
    db: DbSession,
    _: CurrentPrincipal,
    year_from: int | None = Query(default=None, ge=1800, le=2100),
    year_to: int | None = Query(default=None, ge=1800, le=2100),
    event_type: list[str] = Query(default=[]),
    limit: int = Query(500, ge=1, le=2000),
) -> TimelineResponse:
    stmt = select(Event)
    if year_from is not None:
        stmt = stmt.where(Event.year.isnot(None), Event.year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Event.year.isnot(None), Event.year <= year_to)
    if event_type:
        stmt = stmt.where(Event.event_type.in_(event_type))
    events = db.scalars(
        stmt.order_by(Event.event_date.asc().nullslast(), Event.year.asc()).limit(limit)
    ).all()

    published = _published_ids()
    doc_counts = dict(
        db.execute(
            select(EventDocument.event_id, func.count())
            .join(published, EventDocument.document_id == published.c.id)
            .group_by(EventDocument.event_id)
        ).all()
    )
    # An event inherits the least-verified status of the published records behind
    # it, so a single unverified text is enough to stop the event reading as
    # primary. Events with no published record are treated as unverified.
    weakest: dict[str, str] = {}
    for event_id, status in db.execute(
        select(EventDocument.event_id, Document.verification_status)
        .join(published, EventDocument.document_id == published.c.id)
    ).all():
        status = str(status)
        if event_id not in weakest or _STATUS_RANK[status] < _STATUS_RANK[weakest[event_id]]:
            weakest[event_id] = status

    out = [
        EventOut(
            id=e.id,
            title=e.title,
            event_date=e.event_date,
            year=e.year,
            date_precision=e.date_precision,
            description=e.description,
            place=e.place_label,
            event_type=e.event_type,
            document_count=int(doc_counts.get(e.id, 0)),
            verification_status=weakest.get(e.id, "unverified_secondary"),
            is_demo=e.is_demo,
        )
        for e in events
    ]
    years = sorted({e.year for e in out if e.year})
    lo = year_from or (years[0] if years else None)
    hi = year_to or (years[-1] if years else None)
    gaps: list[int] = []
    if lo is not None and hi is not None and 0 < (hi - lo) < 400:
        present = set(years)
        gaps = [y for y in range(int(lo), int(hi) + 1) if y not in present]

    notices: list[str] = []
    if not out:
        notices.append(
            "No dated events are indexed yet. The timeline only shows events backed by an "
            "archive source that states a date."
        )
    elif gaps:
        notices.append(
            "Some years have no dated source in the archive. Gaps are shown rather than "
            "filled with estimated dates."
        )
    return TimelineResponse(
        events=out,
        year_from=lo,
        year_to=hi,
        total=len(out),
        years_with_no_data=gaps,
        notices=notices,
    )


@router.get("/topics", response_model=list[TopicOut], summary="Taxonomy topics")
def topics(db: DbSession, _: CurrentPrincipal, limit: int = Query(100, ge=1, le=500)) -> list[TopicOut]:
    published = _published_ids()
    rows = db.execute(
        select(Topic, func.count(TopicLink.document_id))
        .select_from(Topic)
        .outerjoin(TopicLink, TopicLink.topic_id == Topic.id)
        .outerjoin(published, TopicLink.document_id == published.c.id)
        .group_by(Topic.id)
        .order_by(func.count(TopicLink.document_id).desc())
        .limit(limit)
    ).all()
    return [
        TopicOut(
            id=t.id,
            slug=t.slug,
            name=t.name,
            description=t.description,
            document_count=int(count),
            verified=True,
        )
        for t, count in rows
    ]


@router.get("/persons", response_model=list[PersonOut], summary="People in the archive")
def persons(db: DbSession, _: CurrentPrincipal, limit: int = Query(100, ge=1, le=500)) -> list[PersonOut]:
    published = _published_ids()
    rows = db.execute(
        select(Person, func.count(DocumentPerson.document_id))
        .select_from(Person)
        .outerjoin(DocumentPerson, DocumentPerson.person_id == Person.id)
        .outerjoin(published, DocumentPerson.document_id == published.c.id)
        .group_by(Person.id)
        .order_by(func.count(DocumentPerson.document_id).desc())
        .limit(limit)
    ).all()
    return [
        PersonOut(
            id=p.id,
            slug=p.slug,
            name=p.canonical_name,
            birth_year=p.birth_year,
            death_year=p.death_year,
            summary=p.bio,
            document_count=int(count),
        )
        for p, count in rows
    ]


@router.get("/constitutional-articles", summary="Constitutional articles referenced")
def articles(db: DbSession, _: CurrentPrincipal) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ConstitutionalArticle).order_by(ConstitutionalArticle.article_number)
    ).all()
    counts = dict(
        db.execute(
            select(GraphEdge.dst_id, func.count())
            .where(GraphEdge.relation == "cites_article")
            .group_by(GraphEdge.dst_id)
        ).all()
    )
    return [
        {
            "id": a.id,
            "article_number": a.article_number,
            "part": a.part,
            "chapter": a.chapter,
            "title": a.heading,
            "summary": a.summary,
            "source_url": a.source_url,
            "source_note": a.source_note,
            "document_count": int(counts.get(f"CONSTITUTIONAL_ARTICLE:{a.id}", 0)),
        }
        for a in rows
    ]
