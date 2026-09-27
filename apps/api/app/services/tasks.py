"""Background job layer.

Two rules shape this module:

* a job never lies about success. If OCR or a provider is unavailable the job
  ends in ``FAILED`` with a readable reason and, where the work is retryable, a
  bounded number of attempts; it is never marked ``COMPLETED`` with empty output;
* every job records what it actually did in ``result`` (page counts, engine
  names, confidence) so an archivist can see the difference between a scanned
  page that produced no text and a page that was never processed.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.base import session_scope
from app.models.archive import Document, DocumentPage
from app.models.enums import JobKind, JobState, ProcessingState, PublicationStatus
from app.models.ops import ProcessingJob
from app.providers.jobs import get_queue

log = get_logger(__name__)

Handler = Callable[[Session, ProcessingJob], dict[str, Any]]

JOB_BACKOFF_SECONDS = (2, 10, 30)


# --------------------------------------------------------------------------- #
# enqueue
# --------------------------------------------------------------------------- #


def enqueue_job(
    db: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    document_id: str | None = None,
    media_id: str | None = None,
    created_by: str | None = None,
    max_attempts: int = 3,
) -> str:
    """Record a job in the database and hand it to the queue.

    The database row is the source of truth: if the queue is unreachable the job
    stays ``QUEUED`` and a worker picks it up on its next scan, so an upload is
    never silently lost because Redis was down.
    """
    job = ProcessingJob(
        kind=JobKind(kind) if kind in {m.value for m in JobKind} else JobKind.INGEST,
        state=JobState.QUEUED,
        payload=payload or {},
        document_id=document_id or (payload or {}).get("document_id"),
        media_id=media_id or (payload or {}).get("media_id"),
        created_by=created_by,
        max_attempts=max_attempts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        get_queue().enqueue(job.id)
    except Exception as exc:  # noqa: BLE001
        log.warning("queue unavailable, job stays queued", job_id=job.id, error=str(exc))
    return job.id


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #


def _document(db: Session, job: ProcessingJob) -> Document:
    document_id = job.document_id or (job.payload or {}).get("document_id")
    if not document_id:
        raise ValueError("job has no document_id")
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"document {document_id} no longer exists")
    return document


def _handle_finalise(db: Session, job: ProcessingJob) -> dict[str, Any]:
    """Chunk, embed, index, extract the graph and leave the document ready.

    This is what runs after a manual upload. Publication stays a separate,
    deliberate act: nothing becomes public until an archivist publishes it.
    """
    from app.services.ingestion import reindex_document
    from app.services.knowledge import extract_for_document

    document = _document(db, job)
    ok, chunks, embedded = reindex_document(db, document)
    job.progress = 70
    if not ok:
        return {
            "indexed": False,
            "chunks": chunks,
            "embedded": embedded,
            "detail": "No approved text was available to index; nothing was searchable.",
        }
    graph = extract_for_document(db, document, auto_publish=False)
    # Leave the document in a terminal state. Without this it stays "embedding"
    # forever, so an operator cannot tell finished work from abandoned work.
    document.processing_state = (
        ProcessingState.PUBLISHED
        if str(document.publication_status) == str(PublicationStatus.PUBLISHED)
        else ProcessingState.READY
    )
    db.commit()
    return {
        "indexed": True,
        "chunks": chunks,
        "embedded": embedded,
        "graph_entities": len(graph.entities),
        "graph_edges": len(graph.edges),
        "graph_published": graph.published,
        "graph_pending_review": graph.pending,
        "state": str(document.processing_state),
        "detail": "Indexed and ready. Publish when a human has checked the text.",
    }


def _handle_index(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.services.ingestion import reindex_document

    document = _document(db, job)
    ok, chunks, embedded = reindex_document(db, document)
    return {"indexed": ok, "chunks": chunks, "embedded": embedded}


def _handle_ocr(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.providers.ocr import ocr_capability
    from app.services.ingestion import run_ocr_stage

    document = _document(db, job)
    pages = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == document.id, DocumentPage.is_approved.is_(False))
        .order_by(DocumentPage.page_number.nullslast())
    ).all()
    pages = [p for p in pages if p.original_key or p.storage_key]
    if not pages:
        return {"pages": 0, "detail": "No unprocessed page images were found."}

    capability = ocr_capability()
    if not capability.get("available"):
        raise RuntimeError(
            f"No OCR engine is available ({capability.get('detail', 'unknown')}). "
            "The document keeps its scanned pages and stays unsearchable."
        )
    results = []
    for page in pages:
        outcome = run_ocr_stage(
            db, document, page, language=job.payload.get("language"), preset=job.payload.get("preset", "manuscript")
        )
        results.append(
            {
                "page_number": page.page_number,
                "engine": outcome.engine,
                "confidence": outcome.confidence,
                "characters": len(outcome.text or ""),
            }
        )
    db.commit()
    return {
        "pages": len(results),
        "results": results,
        "ocr_status": str(document.ocr_status),
        "detail": "Text is searchable but unreviewed until a human approves each page.",
    }


def _handle_post_ocr_correction(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.services.ocr.postcorrect import correct_text

    document = _document(db, job)
    limit = int(job.payload.get("limit", 50))
    pages = db.scalars(
        select(DocumentPage)
        .where(
            DocumentPage.document_id == document.id,
            DocumentPage.ocr_text.isnot(None),
            DocumentPage.is_approved.is_(False),
        )
        .limit(limit)
    ).all()
    changed = 0
    for page in pages:
        correction = correct_text(page.ocr_text or "", language=page.ocr_language)
        if correction.changed:
            page.ocr_corrected_text = correction.text
            changed += 1
    db.commit()
    return {
        "pages_examined": len(pages),
        "pages_changed": changed,
        "detail": "Corrections are rule-based or model-suggested and remain unreviewed.",
    }


def _handle_graph(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.services.knowledge import extract_for_document

    document = _document(db, job)
    result = extract_for_document(
        db, document, auto_publish=bool(job.payload.get("auto_publish", False))
    )
    return {
        "entities": len(result.entities),
        "relationships": len(result.relationships),
        "pending_review": len(result.pending_review),
    }


def _handle_translate(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.services.ingestion import translate_document

    document = _document(db, job)
    target = (job.payload or {}).get("target_language", "hi")
    return translate_document(
        db, document, target, limit=int(job.payload.get("limit", 200))
    )


def _handle_publish(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.services.ingestion import publish_document

    document = _document(db, job)
    publish_document(db, document)
    return {"document_id": document.id, "state": str(document.processing_state)}


def _handle_integrity_check(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.services.preservation import verify_document

    document = _document(db, job)
    reports = verify_document(db, document)
    failed = [r for r in reports if not r.ok]
    return {
        "checked": len(reports),
        "failed": len(failed),
        "reports": [
            {"scope": r.scope, "key": r.object_key, "ok": r.ok, "detail": r.detail}
            for r in reports
        ],
        "detail": (
            "All preserved objects match their recorded digests."
            if not failed
            else f"{len(failed)} object(s) do not match their recorded digest."
        ),
    }


def _handle_transcribe_media(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.models.media import Media, MediaTranscript
    from app.providers.speech import get_stt_provider

    media_id = job.media_id or (job.payload or {}).get("media_id")
    media = db.get(Media, media_id) if media_id else None
    if media is None:
        raise ValueError(f"media {media_id} no longer exists")
    if not media.storage_key:
        raise ValueError("this media item has no stored audio")
    provider = get_stt_provider()
    if not provider.is_available():
        raise RuntimeError(
            "No speech-to-text provider is configured, so no transcript was created. "
            "The media item stays searchable only by its metadata."
        )
    from app.providers.storage import get_object_store

    audio = get_object_store().get(media.storage_key)
    if audio is None:
        raise ValueError(f"stored audio for {media_id} is missing")
    result = provider.transcribe(audio, language=media.language)
    transcript = db.scalar(
        select(MediaTranscript).where(MediaTranscript.media_id == media.id)
    )
    if transcript is None:
        transcript = MediaTranscript(media_id=media.id, language=result.language or media.language)
        db.add(transcript)
    transcript.text = result.text
    transcript.provider = result.provider
    transcript.model = result.model
    transcript.confidence = result.confidence
    transcript.is_searchable = False  # never searchable until a human reviews it
    db.commit()
    return {
        "media_id": media.id,
        "characters": len(result.text or ""),
        "provider": result.provider,
        "reviewed": False,
        "detail": "Transcript stored but excluded from search until approved.",
    }


JOB_HANDLERS: dict[str, Handler] = {
    JobKind.FINALISE: _handle_finalise,
    JobKind.INGEST: _handle_finalise,
    JobKind.CHUNK: _handle_index,
    JobKind.EMBED: _handle_index,
    JobKind.OCR: _handle_ocr,
    JobKind.POST_OCR_CORRECTION: _handle_post_ocr_correction,
    JobKind.GRAPH: _handle_graph,
    JobKind.TRANSLATE: _handle_translate,
    JobKind.PUBLISH: _handle_publish,
    JobKind.INTEGRITY_CHECK: _handle_integrity_check,
    JobKind.TRANSCRIBE_MEDIA: _handle_transcribe_media,
}


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def claim_next_job(db: Session) -> ProcessingJob | None:
    """Atomically move one QUEUED job to PROCESSING and return it."""
    job = db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.state == JobState.QUEUED)
        .order_by(ProcessingJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.state = JobState.PROCESSING
    job.attempts += 1
    job.started_at = datetime.now(UTC)
    job.heartbeat_at = job.started_at
    db.commit()
    return job


def run_job(job_id: str) -> dict[str, Any]:
    """Run one job to completion. Safe to call from a worker or a test."""
    with session_scope() as db:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            raise ValueError(f"job {job_id} not found")
        if job.state in (JobState.COMPLETED, JobState.CANCELLED):
            return {"job_id": job_id, "state": str(job.state), "skipped": True}
        handler = JOB_HANDLERS.get(job.kind)
        if handler is None:
            job.state = JobState.FAILED
            job.error = f"No handler is registered for job kind '{job.kind}'."
            job.finished_at = datetime.now(UTC)
            db.commit()
            return {"job_id": job_id, "state": str(job.state), "error": job.error}
        job.state = JobState.PROCESSING
        job.attempts += 1
        job.started_at = job.started_at or datetime.now(UTC)
        job.heartbeat_at = job.started_at
        db.commit()

        started = time.perf_counter()
        try:
            result = handler(db, job)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.get(ProcessingJob, job_id)
            assert job is not None
            retryable = job.attempts < job.max_attempts and not isinstance(
                exc, (ValueError, KeyError)
            )
            job.error = f"{type(exc).__name__}: {exc}"
            job.error_type = type(exc).__name__
            job.result = {"traceback": traceback.format_exc(limit=6)}
            job.finished_at = None
            if retryable:
                job.state = JobState.RETRYING
                job.result = {
                    "attempt": job.attempts,
                    "max_attempts": job.max_attempts,
                    "traceback": traceback.format_exc(limit=6),
                }
            else:
                job.state = JobState.FAILED
                job.finished_at = datetime.now(UTC)
            db.commit()
            log.error("job failed", job_id=job_id, kind=str(job.kind), error=str(exc))
            return {
                "job_id": job_id,
                "state": str(job.state),
                "error": job.error,
                "retrying": retryable,
            }

        job = db.get(ProcessingJob, job_id)
        assert job is not None
        job.state = JobState.COMPLETED
        job.progress = 100
        job.stage = None
        job.error = None
        job.error_type = None
        job.result = result
        job.finished_at = datetime.now(UTC)
        job.heartbeat_at = job.finished_at
        db.commit()
        log.info(
            "job completed",
            job_id=job_id,
            kind=str(job.kind),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return {"job_id": job_id, "state": str(job.state), "result": result}


def retry_stuck_jobs(db: Session) -> int:
    """Requeue jobs left mid-flight by a crash. Called on worker start-up."""
    stale = db.scalars(
        select(ProcessingJob)
        .where(
            ProcessingJob.state.in_([JobState.PROCESSING, JobState.RETRYING]),
            ProcessingJob.attempts < ProcessingJob.max_attempts,
        )
        .limit(50)
    ).all()
    for job in stale:
        job.state = JobState.QUEUED
    db.commit()
    return len(stale)
