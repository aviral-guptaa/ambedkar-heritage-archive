"""System endpoints: health, capabilities, statistics, integrity.

`/health` and `/system/capabilities` are the contract the SIH brief depends on:
the UI must know which OCR engine, graph backend, speech provider and object
store are actually live, so it can disable or honestly label features instead of
pretending they work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentPrincipal, DbSession
from app.core.security import require_permission
from app.models.archive import Document
from app.models.enums import PublicationStatus
from app.models.knowledge import Event, GraphEdge, GraphNode
from app.models.media import Media
from app.models.ops import RagQuery, Story
from app.providers.embedding import embedding_capability
from app.providers.graph import graph_capability
from app.providers.jobs import get_queue, queue_capability
from app.providers.llm import llm_capability
from app.providers.ocr import ocr_capability
from app.providers.rerank import rerank_capability
from app.providers.speech import speech_capability
from app.providers.storage import get_object_store, storage_capability
from app.schemas import (
    CapabilitiesResponse,
    ComponentStatus,
    HealthResponse,
    IntegrityReportOut,
    StatsResponse,
)
from app.services.ingestion import document_statistics
from app.services.preservation import archive_integrity_summary

router = APIRouter(tags=["system"])

APP_VERSION = "1.0.0"

Status = str


def _component(name: str, status: Status, detail: str, required: list[str] | None = None):
    return ComponentStatus(name=name, status=status, detail=detail, required_for=required or [])


def _database_status(db: Session) -> ComponentStatus:
    try:
        db.execute(text("SELECT 1"))
        version = db.execute(text("SHOW server_version")).scalar()
        return _component(
            "database",
            "ok",
            f"PostgreSQL {version} reachable",
            ["search", "rag", "graph", "timeline", "admin"],
        )
    except Exception as exc:  # noqa: BLE001
        return _component("database", "unavailable", f"database unreachable: {exc}")


def _snapshot() -> dict[str, Any]:
    """One pass over every provider so health and capabilities agree."""
    return {
        "storage": storage_capability(),
        "graph": graph_capability(),
        "queue": queue_capability(),
        "ocr": ocr_capability(),
        "embedding": embedding_capability(),
        "reranker": rerank_capability(),
        "llm": llm_capability(),
        "speech": speech_capability(),
    }


def _notices(s: dict[str, Any]) -> list[str]:
    """Plain statements of what is not available right now."""
    notes: list[str] = []
    if not s["ocr"].get("available"):
        notes.append(
            "OCR is unavailable. Scans without a text layer cannot be digitised until "
            "Tesseract or RapidOCR is installed."
        )
    if s["storage"].get("provider") != "minio":
        notes.append("Objects are preserved on the local filesystem rather than MinIO.")
    if s["graph"].get("backend") != "neo4j":
        notes.append(
            "The knowledge graph is served from the relational mirror; Neo4j is not "
            "connected, so traversal depth is limited."
        )
    if not s["llm"].get("generative"):
        notes.append(
            "The extractive grounded answerer is active: answers quote retrieved "
            "passages rather than generating prose, so they cannot invent facts."
        )
    if not s["speech"]["stt"].get("available"):
        notes.append("Server-side speech-to-text is disabled; the kiosk uses the browser Web Speech API.")
    if not s["speech"]["tts"].get("available"):
        notes.append("Text-to-speech is disabled.")
    if not s["speech"]["translation"].get("available"):
        notes.append("Machine translation is disabled; original-language text is shown as-is.")
    if not s["queue"].get("available"):
        notes.append("The job queue is degraded; ingestion still runs, jobs run on demand.")
    return notes


@router.get("/health", response_model=HealthResponse, summary="Liveness and component health")
def health(db: DbSession, _: CurrentPrincipal) -> HealthResponse:
    s = _snapshot()

    database = _database_status(db)
    storage = _component(
        "storage",
        "ok",
        f"{s['storage'].get('provider')} ({s['storage'].get('objects', 0)} objects)",
        ["documents", "media", "integrity"],
    )
    queue = _component(
        "queue",
        "ok" if s["queue"].get("available") else "degraded",
        f"{s['queue'].get('backend')} backend, depth {s['queue'].get('depth', 0)}, "
        f"{s['queue'].get('workers', 0)} workers",
        ["background jobs"],
    )
    graph = _component(
        "graph",
        "ok" if s["graph"].get("available") else "degraded",
        f"{s['graph'].get('backend')} backend, {s['graph'].get('nodes', 0)} nodes, "
        f"{s['graph'].get('edges', 0)} edges",
        ["knowledge graph"],
    )
    ocr = _component(
        "ocr",
        "ok" if s["ocr"].get("available") else "degraded",
        str(s["ocr"].get("detail", "")),
        ["manuscripts", "digitisation"],
    )
    embedding = _component(
        "embedding",
        "ok",
        f"{s['embedding'].get('provider')} / {s['embedding'].get('model')} / "
        f"dim {s['embedding'].get('dimension')}",
        ["semantic search", "rag"],
    )
    llm = _component(
        "llm",
        "ok",
        str(s["llm"].get("detail", "")),
        ["rag answers"],
    )
    speech = _component(
        "speech",
        "ok"
        if (s["speech"]["stt"].get("available") or s["speech"]["tts"].get("available"))
        else "degraded",
        f"stt={s['speech']['stt'].get('provider')} tts={s['speech']['tts'].get('provider')}",
        ["kiosk voice"],
    )
    translation = _component(
        "translation",
        "ok" if s["speech"]["translation"].get("available") else "degraded",
        str(s["speech"]["translation"].get("detail", "")),
        ["hindi/marathi content"],
    )

    components = [database, storage, queue, graph, ocr, embedding, llm, speech, translation]
    status = "degraded" if any(c.status in ("unavailable", "degraded") for c in components) else "ok"

    return HealthResponse(
        status=status,
        service=settings.app_name,
        version=APP_VERSION,
        environment=settings.environment,
        database=database,
        storage=storage,
        queue=queue,
        graph=graph,
        ocr=ocr,
        embedding=embedding,
        llm=llm,
        speech=speech,
        translation=translation,
        components=components,
        checked_at=datetime.now(UTC),
    )


@router.get(
    "/system/capabilities",
    response_model=CapabilitiesResponse,
    summary="Provider and hardware capabilities",
)
def capabilities(db: DbSession, _: CurrentPrincipal) -> CapabilitiesResponse:
    s = _snapshot()

    languages = db.execute(
        select(Document.language).where(
            Document.publication_status == PublicationStatus.PUBLISHED,
            Document.deleted_at.is_(None),
        )
    ).all()
    archive_languages = sorted({row[0] for row in languages if row[0]})

    features = {
        "ocr": bool(s["ocr"].get("available")),
        "hybrid_search": True,
        "semantic_search": True,
        "rag": True,
        "graph": bool(s["graph"].get("available")),
        "timeline": True,
        "media_playback": True,
        "upload": bool(s["storage"].get("available")),
        "background_jobs": bool(s["queue"].get("available")),
        "integrity_checks": True,
        "speech_to_text": bool(s["speech"]["stt"].get("available")),
        "text_to_speech": bool(s["speech"]["tts"].get("available")),
        "translation": bool(s["speech"]["translation"].get("available")),
    }

    return CapabilitiesResponse(
        storage_backend=str(s["storage"].get("provider")),
        graph_backend=str(s["graph"].get("backend", s["graph"].get("provider"))),
        queue_backend=str(s["queue"].get("backend")),
        ocr=s["ocr"],
        embedding=s["embedding"],
        reranker=s["reranker"],
        llm=s["llm"],
        stt=s["speech"]["stt"],
        tts=s["speech"]["tts"],
        translation=s["speech"]["translation"],
        hardware=settings.hardware.model_dump(),
        features=features,
        notices=_notices(s),
        archive_languages=archive_languages,
        limits={
            "max_upload_bytes": settings.max_upload_bytes,
            "search_max_limit": settings.search_max_limit,
            "rag_max_evidence": settings.rag_max_evidence,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
        },
    )


@router.get("/stats", response_model=StatsResponse, summary="Archive statistics")
def stats(db: DbSession, principal: CurrentPrincipal) -> StatsResponse:
    base = document_statistics(db)
    base["events"] = db.scalar(select(func.count()).select_from(Event)) or 0
    base["media_items"] = db.scalar(select(func.count()).select_from(Media)) or 0
    base["stories"] = (
        db.scalar(select(func.count()).select_from(Story).where(Story.is_published.is_(True))) or 0
    )
    base["graph_nodes"] = db.scalar(select(func.count()).select_from(GraphNode)) or 0
    base["graph_edges"] = db.scalar(select(func.count()).select_from(GraphEdge)) or 0
    base["rag_queries"] = db.scalar(select(func.count()).select_from(RagQuery)) or 0
    # A query counts as answered when it produced a grounded answer, not when it
    # merely was not marked as a refusal.
    base["answered_queries"] = (
        db.scalar(
            select(func.count())
            .select_from(RagQuery)
            .where(RagQuery.grounded.is_(True))
        )
        or 0
    )
    base["unverified_answers"] = (
        db.scalar(
            select(func.count())
            .select_from(RagQuery)
            .where(RagQuery.answer_kind == "unverified_summary")
        )
        or 0
    )
    payload: dict[str, Any] = {**base, "computed_at": datetime.now(UTC)}
    if principal.is_authenticated:
        payload["integrity"] = archive_integrity_summary(db)
    return StatsResponse(**payload)


@router.get(
    "/system/integrity",
    response_model=list[IntegrityReportOut],
    summary="Verify stored objects against recorded checksums",
    dependencies=[Depends(require_permission("integrity:run"))],
)
def integrity(db: DbSession) -> list[IntegrityReportOut]:
    """Hashes every stored object, so it is gated behind a permission."""
    summary = archive_integrity_summary(db, verify=True)
    return [IntegrityReportOut(**r) for r in summary.get("reports", [])]


@router.get("/system/queue", summary="Job queue status")
def queue_status() -> dict[str, Any]:
    q = get_queue()
    return {
        "backend": q.name,
        "available": q.is_available(),
        "depth": q.queue_depth(),
        "workers_alive": q.workers_alive(),
        "object_store": get_object_store().name,
    }
