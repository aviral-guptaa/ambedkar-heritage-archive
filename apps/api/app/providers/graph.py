"""Knowledge graph stores.

The public graph is *always* served from the relational mirror
(``graph_nodes`` / ``graph_edges``) so it works offline and on a single edge
box. When Neo4j is configured and reachable the same curated and approved edges
are additionally projected into Neo4j, which is then used for multi-hop path
queries. Both backends implement the same ``GraphStore`` contract and both are
driven exclusively from database rows — no graph content is hard-coded.
"""

from __future__ import annotations

from collections import defaultdict, deque
from functools import lru_cache

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import session_scope
from app.models.knowledge import GraphEdge, GraphNode
from app.providers.base import (
    GraphEdgeData,
    GraphNodeData,
    GraphStats,
    GraphStore,
    GraphSubgraph,
    ProviderUnavailable,
)

log = get_logger(__name__)


class PostgresGraphStore:
    """Relational graph store with BFS neighbourhood expansion."""

    name = "postgres"

    def is_available(self) -> bool:
        return True

    def upsert_node(self, node: GraphNodeData) -> None:
        with session_scope() as db:
            existing = db.get(GraphNode, node.id)
            if existing is None:
                db.add(
                    GraphNode(
                        id=node.id,
                        label=node.label,
                        entity_type=node.entity_type,
                        name=node.name,
                        year=node.year,
                        summary=node.summary,
                        properties=node.properties,
                    )
                )
            else:
                existing.label = node.label
                existing.name = node.name
                existing.year = node.year
                existing.summary = node.summary
                existing.properties = node.properties

    def upsert_edge(self, edge: GraphEdgeData) -> None:
        with session_scope() as db:
            exists = db.scalar(
                select(GraphEdge.id).where(
                    GraphEdge.id == edge.id,
                )
            )
            if exists:
                db.execute(
                    GraphEdge.__table__.update()
                    .where(GraphEdge.id == edge.id)
                    .values(
                        relation=edge.relation,
                        weight=edge.weight,
                        confidence=edge.confidence,
                        properties=edge.properties,
                    )
                )
            else:
                db.add(
                    GraphEdge(
                        id=edge.id,
                        src_id=edge.source,
                        dst_id=edge.target,
                        relation=edge.relation,
                        weight=edge.weight,
                        confidence=edge.confidence,
                        properties=edge.properties,
                    )
                )

    # -- read ------------------------------------------------------------
    @staticmethod
    def _to_node(row: GraphNode, degree: int = 0) -> GraphNodeData:
        return GraphNodeData(
            id=row.id,
            label=row.label,
            entity_type=row.entity_type,
            name=row.name,
            year=row.year,
            summary=row.summary,
            properties=dict(row.properties or {}),
            degree=degree,
        )

    def neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 1,
        limit: int = 40,
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> GraphSubgraph:
        with session_scope() as db:
            root = db.get(GraphNode, node_id)
            if root is None:
                return GraphSubgraph(nodes=[], edges=[], backend=self.name)

            visited: dict[str, GraphNode] = {root.id: root}
            edges: dict[str, GraphEdge] = {}
            frontier: deque[tuple[str, int]] = deque([(root.id, 0)])

            while frontier and len(visited) < limit:
                current, level = frontier.popleft()
                if level >= depth:
                    continue
                stmt = select(GraphEdge).where(
                    (GraphEdge.src_id == current) | (GraphEdge.dst_id == current)
                )
                if relation_types:
                    stmt = stmt.where(GraphEdge.relation.in_(relation_types))
                rows = db.scalars(stmt.limit(limit)).all()
                for edge in rows:
                    other = edge.dst_id if edge.src_id == current else edge.src_id
                    edges[edge.id] = edge
                    if other in visited:
                        continue
                    other_node = db.get(GraphNode, other)
                    if other_node is None:
                        continue
                    if entity_types and other_node.entity_type not in entity_types:
                        continue
                    if year_from is not None and (
                        other_node.year is None or other_node.year < year_from
                    ):
                        continue
                    if year_to is not None and (other_node.year is None or other_node.year > year_to):
                        continue
                    if len(visited) >= limit:
                        break
                    visited[other] = other_node
                    frontier.append((other, level + 1))

            degree_map: dict[str, int] = defaultdict(int)
            for edge in edges.values():
                degree_map[edge.src_id] += 1
                degree_map[edge.dst_id] += 1

            nodes = [self._to_node(n, degree_map.get(n.id, 0)) for n in visited.values()]
            kept = {n.id for n in nodes}
            edge_rows = [
                GraphEdgeData(
                    id=e.id,
                    source=e.src_id,
                    target=e.dst_id,
                    relation=e.relation,
                    weight=e.weight,
                    confidence=e.confidence,
                    properties=dict(e.properties or {}),
                )
                for e in edges.values()
                if e.src_id in kept and e.dst_id in kept
            ]
            truncated = len(visited) >= limit
            return GraphSubgraph(nodes=nodes, edges=edge_rows, backend=self.name, truncated=truncated)

    def search_nodes(self, query: str, limit: int = 20) -> list[GraphNodeData]:
        pattern = f"%{query.strip()}%"
        with session_scope() as db:
            stmt = (
                select(GraphNode)
                .where(GraphNode.name.ilike(pattern) | GraphNode.label.ilike(pattern))
                .order_by(func.length(GraphNode.name))
                .limit(limit)
            )
            rows = db.scalars(stmt).all()
            out: list[GraphNodeData] = []
            for row in rows:
                degree = (
                    db.scalar(
                        select(func.count())
                        .select_from(GraphEdge)
                        .where((GraphEdge.src_id == row.id) | (GraphEdge.dst_id == row.id))
                    )
                    or 0
                )
                out.append(self._to_node(row, int(degree)))
            return out

    def stats(self) -> GraphStats:
        with session_scope() as db:
            node_count = db.scalar(select(func.count()).select_from(GraphNode)) or 0
            edge_count = db.scalar(select(func.count()).select_from(GraphEdge)) or 0
            by_type = {
                r[0]: int(r[1])
                for r in db.execute(
                    select(GraphNode.entity_type, func.count()).group_by(GraphNode.entity_type)
                ).all()
            }
            rel = {
                r[0]: int(r[1])
                for r in db.execute(
                    select(GraphEdge.relation, func.count()).group_by(GraphEdge.relation)
                ).all()
            }
            return GraphStats(self.name, node_count, edge_count, by_type, rel)

    def path_between(self, source: str, target: str, max_depth: int = 4) -> GraphSubgraph:
        with session_scope() as db:
            if db.get(GraphNode, source) is None or db.get(GraphNode, target) is None:
                return GraphSubgraph(nodes=[], edges=[], backend=self.name)
            visited = {source, target}
            edges: dict[str, GraphEdge] = {}
            frontier: deque[tuple[str, deque]] = deque([(source, deque([(source, [])]))])
            found: list[str] | None = None
            while frontier and found is None:
                current, path = frontier.popleft()
                if len(path) > max_depth:
                    continue
                rows = db.scalars(
                    select(GraphEdge).where(
                        (GraphEdge.src_id == current) | (GraphEdge.dst_id == current)
                    )
                ).all()
                for edge in rows:
                    nxt = edge.dst_id if edge.src_id == current else edge.src_id
                    if nxt in [p[0] for p in path]:
                        continue
                    trail = path + [(nxt, [edge.id])]
                    if nxt == target:
                        found = [i for _n, ids in trail for i in ids]
                        for eid in found:
                            row = db.get(GraphEdge, eid)
                            if row:
                                edges[eid] = row
                        break
                    if nxt not in visited and len(visited) < 200:
                        visited.add(nxt)
                        frontier.append((nxt, deque(trail)))
            if found is None:
                return GraphSubgraph(nodes=[], edges=[], backend=self.name)
            node_rows = [db.get(GraphNode, nid) for nid in visited]
            nodes = [self._to_node(n) for n in node_rows if n is not None]
            return GraphSubgraph(
                nodes=nodes,
                edges=[
                    GraphEdgeData(
                        id=e.id,
                        source=e.src_id,
                        target=e.dst_id,
                        relation=e.relation,
                        weight=e.weight,
                        confidence=e.confidence,
                        properties=dict(e.properties or {}),
                    )
                    for e in edges.values()
                ],
                backend=self.name,
            )

    def clear(self) -> int:
        with session_scope() as db:
            count = db.scalar(select(func.count()).select_from(GraphEdge)) or 0
            db.execute(delete(GraphEdge))
            db.execute(delete(GraphNode))
        return int(count)


class Neo4jGraphStore:
    """Neo4j projection. Requires a reachable bolt endpoint."""

    name = "neo4j"

    def __init__(self) -> None:
        self._driver = None
        self._error: str | None = None

    def _ensure(self):  # noqa: ANN202
        if self._driver is not None:
            return self._driver
        if self._error:
            raise ProviderUnavailable(self._error)
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
            )
            driver.verify_connectivity()
            self._driver = driver
            return driver
        except Exception as exc:  # noqa: BLE001
            self._error = f"Neo4j unavailable at {settings.neo4j_uri}: {exc}"
            raise ProviderUnavailable(self._error) from exc

    def is_available(self) -> bool:
        try:
            self._ensure()
            return True
        except ProviderUnavailable:
            return False

    def upsert_node(self, node: GraphNodeData) -> None:
        with self._ensure().session(database=settings.neo4j_database) as session:
            session.run(
                """
                MERGE (n:Entity {id: $id})
                SET n.label = $label, n.entity_type = $entity_type, n.name = $name,
                    n.year = $year, n.summary = $summary, n.props = $props
                """,
                id=node.id,
                label=node.label,
                entity_type=node.entity_type,
                name=node.name,
                year=node.year,
                summary=node.summary,
                props=node.properties,
            )

    def upsert_edge(self, edge: GraphEdgeData) -> None:
        # Neo4j 5 has no dynamic relationship types; store the type as a property
        # and index on it so filtering stays a single query.
        with self._ensure().session(database=settings.neo4j_database) as session:
            session.run(
                """
                MATCH (a:Entity {id: $src}) MATCH (b:Entity {id: $dst})
                MERGE (a)-[r:LINK {rel: $rel}]->(b)
                SET r.weight = $weight, r.confidence = $confidence, r.props = $props
                """,
                src=edge.source,
                dst=edge.target,
                rel=edge.relation,
                weight=edge.weight,
                confidence=edge.confidence,
                props=edge.properties,
            )

    def neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 1,
        limit: int = 40,
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> GraphSubgraph:
        rel_filter = "AND rel.rel IN $rels" if relation_types else ""
        type_filter = "AND n.entity_type IN $types" if entity_types else ""
        year_filter = (
            "AND ($yfrom IS NULL OR (n.year IS NOT NULL AND n.year >= $yfrom))"
            "AND ($yto IS NULL OR (n.year IS NOT NULL AND n.year <= $yto))"
        )
        query = f"""
        MATCH (c:Entity {{id: $id}})-[rel:LINK]-(n:Entity)
        WHERE (1) {rel_filter} {type_filter} {year_filter}
        RETURN c, rel, n, type(rel) AS unused
        LIMIT $limit
        """
        with self._ensure().session(database=settings.neo4j_database) as session:
            rows = session.run(
                query,
                id=node_id,
                rels=relation_types or [],
                types=entity_types or [],
                yfrom=year_from,
                yto=year_to,
                limit=limit,
            ).data()
        return self._subgraph_from_neo4j(rows, center_id=node_id)

    def _subgraph_from_neo4j(self, rows: list[dict], center_id: str) -> GraphSubgraph:
        nodes: dict[str, GraphNodeData] = {}
        edges: dict[str, GraphEdgeData] = {}
        for row in rows:
            for key in ("c", "n"):
                raw = row.get(key)
                if raw is None:
                    continue
                nodes[raw["id"]] = GraphNodeData(
                    id=raw["id"],
                    label=raw.get("label") or raw["id"],
                    entity_type=raw.get("entity_type") or "PERSON",
                    name=raw.get("name") or raw["id"],
                    year=raw.get("year"),
                    summary=raw.get("summary"),
                    properties=dict(raw.get("props") or {}),
                )
            rel = row.get("rel")
            if rel is not None:
                eid = f"{rel.get('rel')}::{rel.element_id}"
                edges[eid] = GraphEdgeData(
                    id=eid,
                    source=rel.get("src") or rel.start_node.get("id"),
                    target=rel.get("dst") or rel.end_node.get("id"),
                    relation=rel.get("rel") or "RELATED_TO",
                    weight=float(rel.get("weight") or 1.0),
                    confidence=float(rel.get("confidence") or 1.0),
                )
        return GraphSubgraph(nodes=list(nodes.values()), edges=list(edges.values()), backend=self.name)

    def search_nodes(self, query: str, limit: int = 20) -> list[GraphNodeData]:
        with self._ensure().session(database=settings.neo4j_database) as session:
            rows = session.run(
                """
                MATCH (n:Entity)
                WHERE toLower(n.name) CONTAINS toLower($q) OR toLower(n.label) CONTAINS toLower($q)
                RETURN n LIMIT $limit
                """,
                q=query,
                limit=limit,
            ).data()
        return [
            GraphNodeData(
                id=r["n"]["id"],
                label=r["n"].get("label") or r["n"]["id"],
                entity_type=r["n"].get("entity_type") or "PERSON",
                name=r["n"].get("name") or r["n"]["id"],
                year=r["n"].get("year"),
                summary=r["n"].get("summary"),
                properties=dict(r["n"].get("props") or {}),
            )
            for r in rows
        ]

    def stats(self) -> GraphStats:
        with self._ensure().session(database=settings.neo4j_database) as session:
            nodes = session.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"]
            edges = session.run("MATCH ()-[r:LINK]->() RETURN count(r) AS c").single()["c"]
            by_type = {
                r["t"]: r["c"]
                for r in session.run(
                    "MATCH (n:Entity) RETURN n.entity_type AS t, count(n) AS c"
                ).data()
            }
            rel = {
                r["t"]: r["c"]
                for r in session.run(
                    "MATCH ()-[r:LINK]->() RETURN r.rel AS t, count(r) AS c"
                ).data()
            }
        return GraphStats(self.name, int(nodes), int(edges), dict(by_type), dict(rel))

    def path_between(self, source: str, target: str, max_depth: int = 4) -> GraphSubgraph:
        with self._ensure().session(database=settings.neo4j_database) as session:
            rows = session.run(
                """
                MATCH p = (a:Entity {id: $src})-[:LINK*1..%d]->(b:Entity {id: $dst})
                RETURN p LIMIT 1
                """
                % max_depth,
                src=source,
                dst=target,
            ).data()
        if not rows:
            return GraphSubgraph(nodes=[], edges=[], backend=self.name)
        path = rows[0]["p"]
        nodes = [
            GraphNodeData(
                id=n["id"],
                label=n.get("label") or n["id"],
                entity_type=n.get("entity_type") or "PERSON",
                name=n.get("name") or n["id"],
                year=n.get("year"),
                summary=n.get("summary"),
            )
            for n in path.nodes
        ]
        edges = [
            GraphEdgeData(
                id=str(i),
                source=path.start_node["id"] if i == 0 else "",
                target=path.end_node["id"] if i == len(path.relationships) - 1 else "",
                relation=r.get("rel") or "RELATED_TO",
            )
            for i, r in enumerate(path.relationships)
        ]
        for i, rel in enumerate(path.relationships):
            edges[i].source = path.nodes[i]["id"]
            edges[i].target = path.nodes[i + 1]["id"]
        return GraphSubgraph(nodes=nodes, edges=edges, backend=self.name)

    def clear(self) -> int:
        with self._ensure().session(database=settings.neo4j_database) as session:
            session.run("MATCH (n) DETACH DELETE n")
        return 0


class CompositeGraphStore:
    """Serves reads from Postgres, mirrors writes into Neo4j when available."""

    name = "composite"

    def __init__(self, primary: GraphStore, mirror: GraphStore | None) -> None:
        self.primary = primary
        self.mirror = mirror

    def is_available(self) -> bool:
        return True

    def upsert_node(self, node: GraphNodeData) -> None:
        self.primary.upsert_node(node)
        if self.mirror:
            try:
                self.mirror.upsert_node(node)
            except ProviderUnavailable:
                pass

    def upsert_edge(self, edge: GraphEdgeData) -> None:
        self.primary.upsert_edge(edge)
        if self.mirror:
            try:
                self.mirror.upsert_edge(edge)
            except ProviderUnavailable:
                pass

    def neighborhood(self, node_id: str, **kwargs):  # noqa: ANN003, ANN201
        return self.primary.neighborhood(node_id, **kwargs)

    def search_nodes(self, query: str, limit: int = 20) -> list[GraphNodeData]:
        return self.primary.search_nodes(query, limit)

    def stats(self) -> GraphStats:
        return self.primary.stats()

    def path_between(self, source: str, target: str, max_depth: int = 4) -> GraphSubgraph:
        return self.primary.path_between(source, target, max_depth)

    def clear(self) -> int:
        return self.primary.clear()


@lru_cache(maxsize=1)
def get_graph_store() -> GraphStore:
    choice = settings.graph_backend
    if choice == "neo4j":
        return Neo4jGraphStore()
    if choice == "postgres":
        return PostgresGraphStore()
    mirror: GraphStore | None = None
    if settings.neo4j_password and settings.neo4j_password != "please-change-me":
        candidate = Neo4jGraphStore()
        if candidate.is_available():
            mirror = candidate
        else:
            log.info("GRAPH_BACKEND=auto but Neo4j unreachable; using relational mirror only")
    return CompositeGraphStore(PostgresGraphStore(), mirror)


def graph_capability() -> dict:
    store = get_graph_store()
    info: dict = {"available": True, "provider": store.name}
    try:
        stats = store.stats()
        info.update(
            {
                "nodes": stats.node_count,
                "edges": stats.edge_count,
                "backend": stats.backend,
            }
        )
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)[:200]
    if isinstance(store, CompositeGraphStore):
        info["mirror"] = store.mirror.name if store.mirror else "not configured"
    return info


def reset_graph_cache() -> None:
    get_graph_store.cache_clear()


def sync_neo4j(db: Session, limit: int = 5000) -> dict[str, int]:  # pragma: no cover
    """Project the relational mirror into Neo4j (admin maintenance action)."""
    store = Neo4jGraphStore()
    nodes = db.scalars(select(GraphNode).limit(limit)).all()
    for node in nodes:
        store.upsert_node(PostgresGraphStore._to_node(node))
    edges = db.scalars(select(GraphEdge).limit(limit)).all()
    for edge in edges:
        store.upsert_edge(
            GraphEdgeData(
                id=edge.id,
                source=edge.src_id,
                target=edge.dst_id,
                relation=edge.relation,
                weight=edge.weight,
                confidence=edge.confidence,
                properties=dict(edge.properties or {}),
            )
        )
    return {"nodes": len(nodes), "edges": len(edges)}
