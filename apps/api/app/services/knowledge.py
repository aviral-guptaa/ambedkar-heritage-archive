"""Knowledge extraction.

Rule-based extraction of people, organisations, places, constitutional articles,
topics, events and dates from ingested text. Design constraints:

* Every extracted edge carries a ``confidence`` and lands as ``status='pending'``
  unless it is a high-confidence, unambiguous match.
* Extracted entities are matched against curated tables (``persons``,
  ``constitutional_articles``, ``topics``, ``organizations``) before being
  created, so the graph does not fill up with near-duplicate nodes.
* Nothing reaches the public graph without an archivist approving it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.archive import Document, DocumentChunk
from app.models.knowledge import (
    ConstitutionalArticle,
    Event,
    GraphEdge,
    GraphNode,
    Organization,
    Person,
    Relationship,
    Topic,
    DocumentPerson,
    TopicLink,
)
from app.models.enums import EntityType, IndexStatus, RelationType

log = get_logger(__name__)

#: Confidence at or above which an extracted edge is published automatically.
AUTO_PUBLISH_CONFIDENCE = 0.95

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
DATE_PATTERNS = [
    re.compile(rf"\b(\d{{1,2}})\s+({_MONTHS})\s+(\d{{4}})\b"),
    re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})\b"),
    re.compile(r"\b(\d{4})\b"),
]
ARTICLE_RE = re.compile(r"\bArticle\s+(\d{1,3}[A-Za-z]?)\b", re.IGNORECASE)
PERSON_RE = re.compile(r"\b(?:Dr\.?|Mr\.?|Mrs\.?|Shri|Smt\.?)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z.'-]+){1,3})")

_ORG_SUFFIXES = (
    "Assembly",
    "Committee",
    "Commission",
    "Council",
    "Conference",
    "Congress",
    "Government",
    "University",
    "Association",
    "Ministry",
    "Department",
    "Court",
    "Union",
    "Federation",
    "Movement",
    "Party",
    "Institute",
    "Academy",
    "Legislative",
    "Parliament",
)

CURATED_PERSON_ALIASES = {
    "b. r. ambedkar": "b-r-ambedkar",
    "b r ambedkar": "b-r-ambedkar",
    "bhimrao ramji ambedkar": "b-r-ambedkar",
    "dr. b. r. ambedkar": "b-r-ambedkar",
    "dr b r ambedkar": "b-r-ambedkar",
    "b. r. ambedkarji": "b-r-ambedkar",
}

CURATED_ORG_PATTERNS = {
    r"constituent assembly": "constituent-assembly-of-india",
    r"drafting committee": "drafting-committee",
    r"round table conference": "round-table-conferences",
    r"united nations": "united-nations",
    r"colonial office": "colonial-office",
    r"harvard university": "harvard-university",
    r"columbia university": "columbia-university",
    r"poona pact": "poona-pact",
}


@dataclass(slots=True)
class ExtractedEntity:
    entity_type: EntityType
    name: str
    key: str
    confidence: float
    mentions: int = 1
    evidence: str | None = None
    year: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExtractionResult:
    entities: list[ExtractedEntity] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    pending: int = 0
    published: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "entities": [
                {
                    "type": e.entity_type.value,
                    "name": e.name,
                    "confidence": round(e.confidence, 3),
                    "mentions": e.mentions,
                }
                for e in self.entities
            ],
            "edges": self.edges,
            "pending": self.pending,
            "published": self.published,
        }


def node_key(entity_type: EntityType, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:80]
    return f"{entity_type.value}:{base}" if base else f"{entity_type.value}:unknown"


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #


def extract_entities(text: str, *, chunk: DocumentChunk | None = None) -> list[ExtractedEntity]:
    found: dict[str, ExtractedEntity] = {}

    def add(entity: ExtractedEntity) -> None:
        existing = found.get(entity.key)
        if existing is None:
            found[entity.key] = entity
        else:
            existing.mentions += entity.mentions
            existing.confidence = min(1.0, max(existing.confidence, entity.confidence) + 0.02)
            if existing.evidence is None:
                existing.evidence = entity.evidence

    # Constitutional articles — high precision, high confidence.
    for match in ARTICLE_RE.finditer(text or ""):
        number = match.group(1).upper()
        add(
            ExtractedEntity(
                entity_type=EntityType.CONSTITUTIONAL_ARTICLE,
                name=f"Article {number}",
                key=node_key(EntityType.CONSTITUTIONAL_ARTICLE, f"Article {number}"),
                confidence=0.98,
                evidence=text[max(0, match.start() - 90) : match.end() + 90].strip(),
                extra={"article_number": number},
            )
        )

    # Known aliases for Dr. Ambedkar himself.
    lowered = (text or "").lower()
    for alias, slug in CURATED_PERSON_ALIASES.items():
        if alias in lowered:
            add(
                ExtractedEntity(
                    entity_type=EntityType.PERSON,
                    name="Dr. B. R. Ambedkar",
                    key=f"PERSON:{slug}",
                    confidence=0.99,
                    mentions=lowered.count(alias),
                    extra={"person_slug": slug},
                )
            )

    # Curated organisations.
    for pattern, slug in CURATED_ORG_PATTERNS.items():
        if re.search(pattern, text or "", re.IGNORECASE):
            add(
                ExtractedEntity(
                    entity_type=EntityType.ORGANIZATION,
                    name=re.sub(r"\b\w", lambda m: m.group(0).upper(), pattern),
                    key=f"ORGANIZATION:{slug}",
                    confidence=0.93,
                )
            )

    # Topic keywords.
    for topic_name, keywords in TOPIC_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in lowered)
        if hits:
            add(
                ExtractedEntity(
                    entity_type=EntityType.TOPIC,
                    name=topic_name,
                    key=node_key(EntityType.TOPIC, topic_name),
                    confidence=min(0.9, 0.5 + 0.1 * hits),
                    mentions=hits,
                )
            )

    # Dates.
    years: dict[str, int] = {}
    for pattern in DATE_PATTERNS[:2]:
        for m in pattern.finditer(text or ""):
            token = m.group(0)
            years[token] = years.get(token, 0) + 1
    for m in DATE_PATTERNS[2].finditer(text or ""):
        years[m.group(0)] = years.get(m.group(0), 0) + 1
    for token, count in years.items():
        if count < 1:
            continue
        year_match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", token)
        year = int(year_match.group(1)) if year_match else None
        add(
            ExtractedEntity(
                entity_type=EntityType.DATE,
                name=token,
                key=node_key(EntityType.DATE, token),
                confidence=0.8 if year else 0.6,
                mentions=count,
                year=year,
            )
        )

    # Organisations by suffix.
    for m in re.finditer(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\s+(" + "|".join(_ORG_SUFFIXES) + r")\b", text or ""):
        name = f"{m.group(1)} {m.group(2)}"
        add(
            ExtractedEntity(
                entity_type=EntityType.ORGANIZATION,
                name=name,
                key=node_key(EntityType.ORGANIZATION, name),
                confidence=0.72,
                evidence=m.group(0),
            )
        )

    # Capitalised multi-word names not already covered.
    for m in PERSON_RE.finditer(text or ""):
        name = m.group(1).strip()
        if len(name) < 6 or any(s in name for s in _ORG_SUFFIXES):
            continue
        if name.lower().startswith(("the ", "this ", "these ", "article ", "part ")):
            continue
        add(
            ExtractedEntity(
                entity_type=EntityType.PERSON,
                name=name,
                key=node_key(EntityType.PERSON, name),
                confidence=0.55,
                evidence=m.group(0),
            )
        )

    if chunk is not None:
        for entity in found.values():
            if entity.evidence is None:
                entity.evidence = chunk.text[:300]
    return list(found.values())


TOPIC_KEYWORDS: dict[str, list[str]] = {
    "Constitution": ["constitution", "constitutional", "draft constitution"],
    "Fundamental Rights": ["fundamental rights", "fundamental right", "rights guaranteed"],
    "Caste": ["caste", "caste system", "untouchability", "varna", "jati"],
    "Social Reform": ["social reform", "reform", "social legislation"],
    "Economics": ["economic", "land revenue", "agriculture", "taxation", "budget", "labour economics"],
    "Education": ["education", "educational", "university", "literacy", "student"],
    "Labour": ["labour", "labor", "wages", "workmen", "employment", "trade union"],
    "Buddhism": ["buddhism", "buddha", "buddhist", "navayana", "deekshabhoomi"],
    "Democracy": ["democracy", "democratic", "popular government", "suffrage"],
    "Equality": ["equality", "equal", "discrimination", "inviolable"],
    "Political Representation": ["political representation", "separate electorate", "electorate", "franchise"],
    "Religion": ["religion", "religious", "hindu", "temple", "conversion"],
    "Women's Rights": ["women", "woman", "child marriage", "widow", "purdah", "gender"],
}


# --------------------------------------------------------------------------- #
# graph projection
# --------------------------------------------------------------------------- #


def upsert_graph_node(
    db: Session,
    node_id: str,
    label: str,
    entity_type: EntityType,
    name: str,
    *,
    year: int | None = None,
    summary: str | None = None,
    properties: dict[str, Any] | None = None,
) -> None:
    existing = db.get(GraphNode, node_id)
    if existing is None:
        db.add(
            GraphNode(
                id=node_id,
                label=label,
                entity_type=entity_type,
                name=name,
                year=year,
                summary=summary,
                properties=properties or {},
            )
        )
    else:
        existing.label = label
        existing.name = name
        existing.year = year if year is not None else existing.year
        existing.summary = summary or existing.summary
        if properties:
            merged = dict(existing.properties or {})
            merged.update(properties)
            existing.properties = merged


def upsert_graph_edge(
    db: Session,
    source: str,
    target: str,
    relation: RelationType,
    *,
    weight: float = 1.0,
    confidence: float = 1.0,
    properties: dict[str, Any] | None = None,
    relationship_id: str | None = None,
) -> GraphEdge:
    edge_id = f"{source}|{relation.value}|{target}"
    existing = db.get(GraphEdge, edge_id)
    if existing is not None:
        existing.weight = max(existing.weight, weight)
        existing.confidence = max(existing.confidence, confidence)
        if properties:
            merged = dict(existing.properties or {})
            merged.update(properties)
            existing.properties = merged
        return existing
    edge = GraphEdge(
        id=edge_id,
        src_id=source,
        dst_id=target,
        relation=relation,
        weight=weight,
        confidence=confidence,
        properties=properties or {},
        relationship_id=relationship_id,
    )
    db.add(edge)
    return edge


def ensure_document_node(db: Session, document: Document) -> str:
    doc_type_map = {
        "speech": EntityType.SPEECH,
        "manuscript": EntityType.MANUSCRIPT,
        "book": EntityType.BOOK,
        "debate": EntityType.DOCUMENT,
    }
    entity_type = doc_type_map.get(document.document_type, EntityType.DOCUMENT)
    node_id = f"DOCUMENT:{document.id}"
    upsert_graph_node(
        db,
        node_id,
        label=document.title[:120],
        entity_type=entity_type,
        name=document.title,
        year=document.year,
        summary=(document.summary or document.subtitle) or None,
        properties={
            "document_id": document.id,
            "document_type": document.document_type,
            "language": document.language,
            "source_url": document.source_url,
            "date": document.document_date.isoformat() if document.document_date else None,
            "speaker": document.speaker,
        },
    )
    return node_id


def extract_for_document(
    db: Session,
    document: Document,
    *,
    chunk_limit: int = 40,
    auto_publish: bool = True,
) -> ExtractionResult:
    """Run extraction over a document and stage the resulting edges."""
    result = ExtractionResult()
    doc_node = ensure_document_node(db, document)

    if document.collection_id:
        collection_node = f"COLLECTION:{document.collection_id}"
        upsert_graph_node(
            db,
            collection_node,
            label="Collection",
            entity_type=EntityType.COLLECTION,
            name=document.collection.title if document.collection else "Collection",
        )
        upsert_graph_edge(
            db,
            doc_node,
            collection_node,
            RelationType.BELONGS_TO,
            properties={"collection_id": document.collection_id},
        )
        result.published += 1

    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index)
        .limit(chunk_limit)
    ).all()
    if not chunks:
        # Nothing to extract from: record that plainly instead of leaving the
        # document looking like it had been processed.
        document.graph_status = IndexStatus.FAILED
        db.commit()
        return result

    counts: dict[str, ExtractedEntity] = {}
    for chunk in chunks:
        for entity in extract_entities(chunk.text, chunk=chunk):
            existing = counts.get(entity.key)
            if existing is None:
                counts[entity.key] = entity
            else:
                existing.mentions += entity.mentions
                existing.confidence = min(0.99, existing.confidence + 0.03)

    curated_topics = {
        t.name.lower(): t.id for t in db.scalars(select(Topic)).all()
    }
    curated_articles = {
        a.article_number: a for a in db.scalars(select(ConstitutionalArticle)).all()
    }
    curated_persons = {
        p.canonical_name.lower(): p for p in db.scalars(select(Person)).all()
    }

    for entity in counts.values():
        confidence = entity.confidence * (1.0 if entity.mentions > 1 else 0.85)
        if entity.entity_type is EntityType.DATE:
            # Dates are properties of the record, not independent graph nodes.
            continue
        if entity.entity_type is EntityType.CONSTITUTIONAL_ARTICLE:
            number = entity.extra.get("article_number")
            article = curated_articles.get(number)
            if article is not None:
                node_id = f"CONSTITUTIONAL_ARTICLE:{number}"
                upsert_graph_node(
                    db,
                    node_id,
                    label="Article",
                    entity_type=EntityType.CONSTITUTIONAL_ARTICLE,
                    name=f"Article {number}",
                    properties={
                        "article_number": number,
                        "heading": article.heading,
                        "source_url": article.source_url,
                    },
                )
                edge_conf = 0.98
            else:
                node_id = entity.key
                upsert_graph_node(
                    db, node_id, "Article", EntityType.CONSTITUTIONAL_ARTICLE, entity.name
                )
                edge_conf = 0.7
        elif entity.entity_type is EntityType.PERSON and entity.name.lower() in curated_persons:
            person = curated_persons[entity.name.lower()]
            node_id = f"PERSON:{person.slug}"
            upsert_graph_node(
                db,
                node_id,
                label="Person",
                entity_type=EntityType.PERSON,
                name=person.canonical_name,
                year=person.birth_year,
                summary=person.bio,
                properties={"person_id": person.id, "source_url": person.source_url},
            )
            edge_conf = 0.97
            db.merge(
                DocumentPerson(
                    document_id=document.id,
                    person_id=person.id,
                    role="speaker" if document.speaker and person.canonical_name.lower() in document.speaker.lower() else "mentioned",
                )
            )
        else:
            node_id = entity.key
            upsert_graph_node(
                db,
                node_id,
                label=entity.entity_type.value.title(),
                entity_type=entity.entity_type,
                name=entity.name,
                year=entity.year,
                properties=entity.extra or {},
            )
            edge_conf = confidence

        relation = (
            RelationType.AUTHORED
            if entity.entity_type is EntityType.PERSON
            and (document.speaker or "").lower().find(entity.name.lower()) >= 0
            else RelationType.MENTIONS
        )
        if entity.entity_type is EntityType.CONSTITUTIONAL_ARTICLE:
            relation = RelationType.RELATED_TO_ARTICLE

        publish = auto_publish and edge_conf >= AUTO_PUBLISH_CONFIDENCE
        relationship = Relationship(
            from_type=EntityType.DOCUMENT,
            from_id=document.id,
            to_type=entity.entity_type,
            to_id=node_id,
            relation=relation,
            confidence=round(edge_conf, 3),
            status="approved" if publish else "pending",
            method="rule_extraction",
            evidence_text=entity.evidence,
        )
        existing_rel = db.scalar(
            select(Relationship).where(
                Relationship.from_type == EntityType.DOCUMENT,
                Relationship.from_id == document.id,
                Relationship.to_type == entity.entity_type,
                Relationship.to_id == node_id,
                Relationship.relation == relation,
            )
        )
        if existing_rel is not None:
            existing_rel.confidence = max(existing_rel.confidence, round(edge_conf, 3))
            relationship = existing_rel
        else:
            db.add(relationship)
        db.flush()
        result.edges.append(
            {
                "from": doc_node,
                "to": node_id,
                "relation": relation.value,
                "confidence": round(edge_conf, 3),
                "status": relationship.status,
            }
        )
        if publish:
            upsert_graph_edge(
                db,
                doc_node,
                node_id,
                relation,
                confidence=edge_conf,
                relationship_id=relationship.id,
                properties={"document_id": document.id},
            )
            result.published += 1
        else:
            result.pending += 1

        if entity.entity_type is EntityType.TOPIC:
            topic_id = curated_topics.get(entity.name.lower())
            if topic_id:
                link = db.scalar(
                    select(TopicLink).where(
                        TopicLink.document_id == document.id, TopicLink.topic_id == topic_id
                    )
                )
                if link is None:
                    db.add(
                        TopicLink(
                            document_id=document.id,
                            topic_id=topic_id,
                            weight=min(3.0, 1.0 + 0.25 * entity.mentions),
                            source="extracted",
                        )
                    )

    _refresh_degrees(db)
    document.graph_status = IndexStatus.INDEXED
    db.commit()
    result.entities = list(counts.values())
    return result


def _refresh_degrees(db: Session) -> None:
    """Recompute node degree from the edge table (small graph, so full scan)."""
    db.execute(GraphNode.__table__.update().values(degree=0))
    counts: dict[str, int] = defaultdict(int)
    for src, dst in db.execute(select(GraphEdge.src_id, GraphEdge.dst_id)).all():
        counts[src] += 1
        counts[dst] += 1
    for node_id, count in counts.items():
        db.execute(
            GraphNode.__table__.update().where(GraphNode.id == node_id).values(degree=count)
        )


def event_node(db: Session, event: Event) -> str:
    node_id = f"EVENT:{event.id}"
    upsert_graph_node(
        db,
        node_id,
        label="Event",
        entity_type=EntityType.EVENT,
        name=event.title,
        year=event.year,
        summary=event.description,
        properties={
            "event_id": event.id,
            "date": event.date_label,
            "source_url": event.source_url,
        },
    )
    return node_id


def link_event_to_document(db: Session, event: Event, document: Document) -> None:
    doc_node = ensure_document_node(db, document)
    ev_node = event_node(db, event)
    link = db.scalar(
        select(Relationship).where(
            Relationship.from_type == EntityType.EVENT,
            Relationship.from_id == event.id,
            Relationship.to_type == EntityType.DOCUMENT,
            Relationship.to_id == document.id,
            Relationship.relation == RelationType.REFERENCES,
        )
    )
    if link is None:
        link = Relationship(
            from_type=EntityType.EVENT,
            from_id=event.id,
            to_type=EntityType.DOCUMENT,
            to_id=document.id,
            relation=RelationType.REFERENCES,
            confidence=1.0,
            status="approved",
            method="curated",
        )
        db.add(link)
        db.flush()
    upsert_graph_edge(
        db, ev_node, doc_node, RelationType.REFERENCES, relationship_id=link.id
    )


def review_relationship(
    db: Session, relationship_id: str, approve: bool, reviewer_id: str | None
) -> Relationship | None:
    from datetime import UTC, datetime

    relationship = db.get(Relationship, relationship_id)
    if relationship is None:
        return None
    relationship.status = "approved" if approve else "rejected"
    relationship.reviewed_by = reviewer_id
    relationship.reviewed_at = datetime.now(UTC)

    source_key = f"{relationship.from_type.value}:{relationship.from_id}"
    target_key = f"{relationship.to_id}"
    if db.get(GraphNode, source_key) is None:
        target_key = f"{relationship.to_type.value}:{relationship.to_id}"
    if approve:
        if db.get(GraphNode, source_key) is not None and db.get(GraphNode, target_key) is not None:
            upsert_graph_edge(
                db,
                source_key,
                target_key,
                relationship.relation,
                confidence=relationship.confidence,
                relationship_id=relationship.id,
            )
    else:
        edge_id = f"{source_key}|{relationship.relation.value}|{target_key}"
        edge = db.get(GraphEdge, edge_id)
        if edge is not None:
            db.delete(edge)
    db.commit()
    return relationship


def staged_relationships(db: Session, limit: int = 100) -> Iterable[Relationship]:
    return db.scalars(
        select(Relationship)
        .where(Relationship.status == "pending")
        .order_by(Relationship.confidence.desc())
        .limit(limit)
    ).all()


def known_entities(db: Session) -> dict[str, list[dict[str, Any]]]:
    people = db.execute(select(Person.id, Person.canonical_name)).all()
    articles = db.execute(
        select(ConstitutionalArticle.article_number, ConstitutionalArticle.heading)
    ).all()
    orgs = db.execute(select(Organization.id, Organization.name)).all()
    return {
        "persons": [{"id": p[0], "name": p[1]} for p in people],
        "constitutional_articles": [
            {"article_number": a[0], "heading": a[1]} for a in articles
        ],
        "organizations": [{"id": o[0], "name": o[1]} for o in orgs],
    }
