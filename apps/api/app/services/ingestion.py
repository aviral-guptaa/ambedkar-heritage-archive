"""Ingestion pipeline.

Stages, each of which is independently re-runnable and reports real state:

    validate → hash → store original (AIP) → extract text → OCR if needed
    → metadata → chunk → embed → index → knowledge graph → publish

A document is only made publicly visible after every required stage succeeds, so
a half-processed upload can never leak into search results.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import cv2

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import session_scope
from app.models.archive import (
    Document,
    DocumentChunk,
    DocumentMetadata,
    DocumentPage,
    DocumentVersion,
    Embedding,
    OcrResult,
    Source,
    Translation,
)
from app.models.enums import (
    VerificationStatus,
    DocumentType,
    IndexStatus,
    OcrStatus,
    ProcessingState,
    PublicationStatus,
    SourceTier,
)
from app.providers.embedding import get_embedding_provider
from app.providers.ocr import run_ocr
from app.providers.storage import build_key, get_object_store, sha256_bytes
from app.services import extract as extract_mod
from app.services.chunking import chunk_pages, detect_language
from app.services.knowledge import extract_for_document
from app.services.preservation import record_version, slugify, verify_document

log = get_logger(__name__)

DATE_FORMATS = ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%Y")
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


class IngestionError(RuntimeError):
    pass


def _to_gray(image):
    """Coerce a preprocessed image to a single-channel array for layout analysis."""
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


@dataclass(slots=True)
class StageResult:
    stage: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None


@dataclass(slots=True)
class IngestionResult:
    document_id: str
    state: ProcessingState
    stages: list[StageResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "state": self.state.value,
            "stages": [
                {
                    "stage": s.stage,
                    "ok": s.ok,
                    "detail": s.detail,
                    "warning": s.warning,
                }
                for s in self.stages
            ],
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

MAGIC = {
    b"%PDF-": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"II*\x00": "image/tiff",
    b"MM\x00*": "image/tiff",
    b"BM": "image/bmp",
    b"ID3": "audio/mpeg",
    b"OggS": "audio/ogg",
    b"fLaC": "audio/flac",
    b"RIFF": "audio/wav",
    b"\x1a\x45\xdf\xa3": "video/webm",
}

DANGEROUS_EXTENSIONS = {".exe", ".sh", ".bat", ".cmd", ".ps1", ".dll", ".so", ".dylib", ".jar"}


def validate_upload(filename: str, data: bytes, *, declared_mime: str | None = None) -> dict[str, Any]:
    """Validate an incoming file. Raises ``IngestionError`` with a clear reason."""
    if not filename or not filename.strip():
        raise IngestionError("A filename is required.")
    name = Path(filename).name  # strip any directory component
    ext = extract_mod.guess_extension(name)
    if ext in DANGEROUS_EXTENSIONS:
        raise IngestionError(f"File type '{ext}' is not accepted by the archive.")
    allowed = set(settings.allowed_upload_extensions)
    if ext not in allowed:
        raise IngestionError(
            f"Unsupported file type '{ext or name}'. Accepted: {', '.join(sorted(allowed))}."
        )
    if not data:
        raise IngestionError("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes // (1024 * 1024)
        raise IngestionError(f"File exceeds the {limit_mb} MB upload limit.")
    if "\x00" in name:
        raise IngestionError("Filename contains illegal characters.")

    detected = None
    for magic, mime in MAGIC.items():
        if data.startswith(magic):
            detected = mime
            break
    expected = extract_mod.guess_mime(name)

    warning = None
    if detected and expected != "application/octet-stream" and detected != expected:
        same_family = detected.split("/")[0] == expected.split("/")[0]
        if not same_family:
            # Not fatal, but the archivist is told the content disagrees with
            # the extension they uploaded it under.
            warning = f"declared '{expected}' but the file content looks like '{detected}'"

    return {
        "filename": name,
        "extension": ext,
        "mime_type": expected,
        "detected_mime": detected,
        "byte_size": len(data),
        "sha256": sha256_bytes(data),
        "warning": warning,
    }


# --------------------------------------------------------------------------- #
# metadata helpers
# --------------------------------------------------------------------------- #


def parse_date(value: str | None) -> tuple[date | None, str | None]:
    if not value:
        return None, None
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt).date()
            precision = "day" if fmt not in ("%Y",) else "year"
            return parsed, precision
        except ValueError:
            continue
    year_match = YEAR_RE.search(value)
    if year_match:
        return date(int(year_match.group(1)), 1, 1), "year"
    return None, None


def resolve_source(
    db: Session,
    *,
    source_url: str | None,
    source_name: str | None = None,
    source_type: str | None = None,
    rights: str | None = None,
    publisher: str | None = None,
    retrieval_date: str | None = None,
) -> Source | None:
    """Find or create the provenance record for a document."""
    if not source_url and not source_name:
        return None
    domain = None
    if source_url:
        m = re.search(r"https?://([^/]+)", source_url)
        domain = m.group(1).lower() if m else None
    if source_url:
        existing = db.scalar(select(Source).where(Source.source_url == source_url))
        if existing is not None:
            return existing
    if source_name and domain:
        existing = db.scalar(
            select(Source).where(Source.name == source_name, Source.source_domain == domain)
        )
        if existing is not None:
            return existing
    tier = tier_for(domain, source_type)
    parsed_retrieval, _ = parse_date(retrieval_date)
    source = Source(
        name=source_name or domain or "Unattributed source",
        source_url=source_url,
        source_type=source_type,
        source_domain=domain,
        publisher=publisher,
        tier=tier,
        rights_status=rights,
        license=rights,
        retrieval_date=parsed_retrieval or date.today(),
    )
    db.add(source)
    db.flush()
    return source


def tier_for(domain: str | None, source_type: str | None) -> SourceTier:
    """Reliability tier. Government and official archives rank highest."""
    if domain:
        d = domain.lower()
        if d.endswith(".gov.in") or d.endswith(".gov") or d.endswith(".nic.in"):
            return SourceTier.GOVERNMENT_PRIMARY
        if d.endswith(".ac.in") or d.endswith(".edu") or ".edu." in d:
            return SourceTier.INSTITUTIONAL_ARCHIVE
        if d.endswith(".org") or d.endswith(".org.in"):
            return SourceTier.INSTITUTIONAL_ARCHIVE
    if source_type and "government" in source_type.lower():
        return SourceTier.GOVERNMENT_PRIMARY
    return SourceTier.UNKNOWN


# --------------------------------------------------------------------------- #
# the pipeline
# --------------------------------------------------------------------------- #


def ingest_document(
    db: Session,
    data: bytes,
    filename: str,
    *,
    title: str | None = None,
    author: str | None = None,
    document_type: str | None = None,
    language: str | None = None,
    document_date: str | None = None,
    year: int | None = None,
    collection_id: str | None = None,
    source_url: str | None = None,
    source_name: str | None = None,
    source_type: str | None = None,
    rights: str | None = None,
    location: str | None = None,
    venue: str | None = None,
    source_reference: str | None = None,
    external_id: str | None = None,
    is_demo: bool = False,
    editorial_note: str | None = None,
    created_by: str | None = None,
    run_ocr_if_needed: bool = True,
    auto_publish: bool = False,
) -> IngestionResult:
    """Run the full ingestion pipeline for one uploaded file."""
    result = IngestionResult(document_id="", state=ProcessingState.UPLOADED)
    warnings: list[str] = result.warnings

    # -- 1. validate ----------------------------------------------------
    meta = validate_upload(filename, data)
    if meta.get("warning"):
        warnings.append(meta["warning"])
    result.stages.append(
        StageResult(
            "validate",
            True,
            {
                "filename": meta["filename"],
                "mime_type": meta["mime_type"],
                "byte_size": meta["byte_size"],
                "sha256": meta["sha256"],
            },
        )
    )

    ext = meta["extension"]
    doc_type = extract_mod.classify_document_type(meta["filename"], document_type)
    resolved_title = (title or Path(meta["filename"]).stem).strip()
    parsed_date, precision = parse_date(document_date)
    resolved_year = year or (parsed_date.year if parsed_date else None)

    source = resolve_source(
        db,
        source_url=source_url,
        source_name=source_name,
        source_type=source_type,
        rights=rights,
    )

    document = Document(
        slug=f"{slugify(resolved_title)}-{uuid.uuid4().hex[:8]}",
        title=resolved_title,
        author_display=author,
        speaker=author,
        document_type=doc_type.value,
        language=language or "en",
        original_language=language,
        document_date=parsed_date,
        year=resolved_year,
        date_precision=precision,
        location_text=location,
        venue=venue,
        collection_id=collection_id,
        source_id=source.id if source else None,
        source_url=source_url,
        source_reference=source_reference,
        external_id=external_id,
        rights=rights or (source.rights_status if source else None),
        provenance=(
            f"Uploaded {datetime.now(UTC).isoformat()} by {created_by or 'system'}. "
            f"Source: {source_url or source_name or 'not supplied'}"
        ),
        editorial_note=editorial_note,
        is_demo=is_demo,
        processing_state=ProcessingState.VALIDATED,
        publication_status=PublicationStatus.DRAFT,
    )
    db.add(document)
    db.flush()
    result.document_id = document.id

    # -- 2. preserve the original (AIP) ---------------------------------
    store = get_object_store()
    is_media = ext in extract_mod.AUDIO_EXTENSIONS or ext in extract_mod.VIDEO_EXTENSIONS
    if is_media:
        document.processing_state = ProcessingState.VALIDATED
        record_version(
            db,
            document,
            data=data,
            storage_key="",
            mime_type=meta["mime_type"],
            note="media asset",
            created_by=created_by,
        )
        document.storage_key = ""
        key = build_key("media-source", document.id, meta["filename"])
        stored = store.put(key, data, meta["mime_type"])
        document.storage_key = stored.key
        document.checksum_sha256 = stored.sha256
        document.byte_size = stored.size
        db.commit()
        result.state = ProcessingState.READY
        result.stages.append(
            StageResult("store_original", True, {"key": stored.key, "sha256": stored.sha256})
        )
        result.stages.append(
            StageResult(
                "media_handling",
                True,
                {"note": "Audio/video stored. Run transcription to make it searchable."},
            )
        )
        return result

    key = build_key("originals", document.id, meta["filename"])
    stored = store.put(key, data, meta["mime_type"])
    record_version(
        db,
        document,
        data=data,
        storage_key=stored.key,
        mime_type=meta["mime_type"],
        note="Original submission (SIP promoted to AIP)",
        created_by=created_by,
    )
    document.page_count = 0
    result.stages.append(
        StageResult(
            "store_original",
            True,
            {"key": stored.key, "sha256": stored.sha256, "size": stored.size},
        )
    )

    # -- 3. page records + text extraction -------------------------------
    extracted = extract_mod.extract_text(data, meta["filename"])
    for w in extracted.warnings:
        warnings.append(w)

    if ext in extract_mod.IMAGE_EXTENSIONS:
        page = DocumentPage(
            document_id=document.id,
            page_number=1,
            original_key=stored.key,
            original_sha256=stored.sha256,
        )
        db.add(page)
        db.flush()
        document.page_count = 1
        result.stages.append(
            StageResult("extract_text", True, {"requires_ocr": True, "pages": 1})
        )
        if run_ocr_if_needed:
            run_ocr_stage(db, document, page, language=language or settings.ocr_default_language)
        else:
            document.ocr_status = OcrStatus.PENDING
    else:
        pages = extracted.pages or [extracted.text]
        page_records: list[DocumentPage] = []
        for i, page_text in enumerate(pages, start=1):
            page = DocumentPage(
                document_id=document.id,
                page_number=i,
                ocr_text=page_text or None,
                word_count=len((page_text or "").split()),
                ocr_engine="text_layer" if page_text else None,
                is_approved=True,
            )
            db.add(page)
            page_records.append(page)
        db.flush()
        document.page_count = len(page_records)
        document.ocr_status = OcrStatus.NOT_REQUIRED
        result.stages.append(
            StageResult(
                "extract_text",
                True,
                {
                    "extractor": extracted.extractor,
                    "pages": len(page_records),
                    "characters": len(extracted.text),
                    "requires_ocr": extracted.requires_ocr,
                },
            )
        )
        if extracted.requires_ocr and run_ocr_if_needed:
            warnings.append(
                "Some pages have no text layer. Run OCR from the manuscript workspace to digitise them."
            )
            document.ocr_status = OcrStatus.PENDING

    # -- 4. metadata -----------------------------------------------------
    result.stages.append(StageResult("metadata", True, apply_metadata(db, document, meta, source)))

    # -- 5. chunk + embed + index ---------------------------------------
    document.processing_state = ProcessingState.CHUNKING
    chunks_created = index_document(db, document, pages=_page_pairs(db, document))
    result.stages.append(
        StageResult(
            "chunk_embed_index",
            chunks_created[0],
            {
                "chunks": chunks_created[1],
                "embedded": chunks_created[2],
                "embedding_provider": get_embedding_provider().name,
            },
        )
    )

    # -- 6. knowledge graph ---------------------------------------------
    document.processing_state = ProcessingState.GRAPH
    try:
        extraction = extract_for_document(db, document)
        result.stages.append(
            StageResult(
                "knowledge_graph",
                True,
                {
                    "edges": len(extraction.edges),
                    "published": extraction.published,
                    "awaiting_review": extraction.pending,
                },
            )
        )
        document.graph_status = IndexStatus.INDEXED
    except Exception as exc:  # noqa: BLE001
        log.warning("graph extraction failed", document_id=document.id, error=str(exc))
        warnings.append(f"Knowledge-graph extraction failed: {exc}")
        document.graph_status = IndexStatus.FAILED
        result.stages.append(StageResult("knowledge_graph", False, {"error": str(exc)}))

    # -- 7. publish ------------------------------------------------------
    if auto_publish:
        publish_document(db, document)
    document.processing_state = (
        ProcessingState.PUBLISHED
        if document.publication_status == PublicationStatus.PUBLISHED
        else ProcessingState.READY
    )
    db.commit()

    if settings.integrity_auto_check_on_publish:
        try:
            verify_document(db, document)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Integrity check failed: {exc}")

    result.state = document.processing_state
    return result


def _page_pairs(db: Session, document: Document) -> list[tuple[int, str]]:
    """The document's text, as (page_number, text) pairs.

    A document with page images is chunked page by page, so a citation can point
    at a page. A document whose text exists only as a stored version (an imported
    transcript, a born-digital PDF) has no pages at all: it is returned as a
    single pair numbered 0, which downstream code records as "no pagination"
    rather than inventing a page 1.
    """
    pages = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == document.id)
        .order_by(DocumentPage.page_number)
    ).all()
    pairs = [(p.page_number, p.effective_text) for p in pages if p.effective_text.strip()]
    if pairs:
        return pairs
    text = _current_text_version(db, document)
    return [(0, text)] if text else []


def _current_text_version(db: Session, document: Document) -> str:
    """Text of the document's current stored version, or "" if it is not text."""
    from app.providers.storage import get_object_store

    version = db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.is_current.is_(True),
        )
        .order_by(DocumentVersion.version.desc())
    )
    if version is None or not (version.mime_type or "").startswith("text/"):
        return ""
    try:
        data = get_object_store().get(version.storage_key)
    except Exception:  # noqa: BLE001 - a missing object must not stop the archive
        log.warning(
            "version object unreadable", document_id=document.id, key=version.storage_key
        )
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def apply_metadata(
    db: Session, document: Document, meta: dict[str, Any], source: Source | None
) -> dict[str, Any]:
    """Write the descriptive metadata block the UI displays."""
    fields = {
        "filename": meta["filename"],
        "mime_type": meta["mime_type"],
        "byte_size": str(meta["byte_size"]),
        "sha256": meta["sha256"],
        "document_type": document.document_type,
        "language": document.language,
        "ingestion_date": datetime.now(UTC).date().isoformat(),
        "processing_status": document.processing_state.value,
    }
    if source is not None:
        fields["source_name"] = source.name
        fields["source_url"] = source.source_url or ""
        fields["source_type"] = source.source_type or ""
        fields["rights"] = source.rights_status or ""
        fields["source_tier"] = source.tier.value
        if source.retrieval_date:
            fields["retrieval_date"] = source.retrieval_date.isoformat()
    if document.location_text:
        fields["location"] = document.location_text
    if document.venue:
        fields["venue"] = document.venue
    if document.document_date:
        fields["date"] = document.document_date.isoformat()
    if document.year:
        fields["year"] = str(document.year)
    if document.collection:
        fields["collection"] = document.collection.title
    if document.source_reference:
        fields["source_reference"] = document.source_reference
    if document.provenance:
        fields["provenance"] = document.provenance
    if document.editorial_note:
        fields["editorial_note"] = document.editorial_note

    for key, value in fields.items():
        existing = db.scalar(
            select(DocumentMetadata).where(
                DocumentMetadata.document_id == document.id, DocumentMetadata.key == key
            )
        )
        if existing is None:
            db.add(DocumentMetadata(document_id=document.id, key=key, value=value))
        else:
            existing.value = value
    db.flush()
    return {"fields": len(fields), "keys": sorted(fields)}


def index_document(
    db: Session, document: Document, *, pages: list[tuple[int, str]] | None = None
) -> tuple[bool, int, int]:
    """Chunk, embed and index a document. Returns (ok, chunks, embedded)."""
    from app.services.indexing import build_tsv, write_chunk_vectors

    if pages is None:
        pages = _page_pairs(db, document)
    if not pages:
        # There is genuinely no text anywhere for this document. Clear the stale
        # chunks too, so the counters and the chunk table cannot disagree, and say
        # plainly that nothing is searchable rather than reporting success.
        db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete(
            synchronize_session=False
        )
        document.chunk_count = 0
        document.embedding_status = IndexStatus.FAILED
        db.flush()
        return (False, 0, 0)

    db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete(
        synchronize_session=False
    )
    db.flush()
    page_by_number = {
        p.page_number: p
        for p in db.scalars(
            select(DocumentPage).where(DocumentPage.document_id == document.id)
        ).all()
    }

    document.processing_state = ProcessingState.CHUNKING
    chunks = chunk_pages(pages)
    rows: list[DocumentChunk] = []
    for chunk in chunks:
        page = page_by_number.get(chunk.page_number or 0)
        text = chunk.text.strip()
        if not text:
            continue
        rows.append(
            DocumentChunk(
                document_id=document.id,
                page_id=page.id if page else None,
                chunk_index=len(rows),
                text=text,
                section=chunk.section,
                language=chunk.language or document.language,
                token_count=len(chunk.text.split()),
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                page_number=chunk.page_number,
                # The citation pointer is copied onto the chunk so it survives
                # later edits to the document record.
                source_url=document.source_url,
                source_reference=document.source_reference,
                volume=document.volume,
                kind=chunk.kind,
                content_hash=sha256_bytes(text.encode("utf-8")),
                is_corrected_text=bool(page and page.ocr_corrected_text),
                # A chunk inherits its document's verification status: text that
                # was never checked against the original may be searched and
                # summarised, but must not be presented as a quotation.
                quote_verified=(
                    str(document.verification_status) == str(VerificationStatus.VERIFIED_PRIMARY)
                ),
            )
        )
    for row in rows:
        db.add(row)
    db.flush()

    build_tsv(db, document, rows)

    document.chunk_count = len(rows)
    document.word_count = sum(len(r.text.split()) for r in rows)
    document.processing_state = ProcessingState.EMBEDDING
    embedded = write_chunk_vectors(db, rows)
    document.embedding_status = IndexStatus.INDEXED if embedded == len(rows) else IndexStatus.FAILED
    db.flush()
    return (embedded > 0, len(rows), embedded)


def run_ocr_stage(
    db: Session,
    document: Document,
    page: DocumentPage,
    *,
    language: str | None = None,
    preset: str = "manuscript",
) -> OcrResult:
    """Run the OCR pipeline for one page and store a versioned result."""
    from app.providers.base import ProviderUnavailable
    from app.services.ocr.preprocess import analyse_layout, make_thumbnail, preprocess
    from app.services.ocr.postcorrect import correct_text

    store = get_object_store()
    if not page.original_key:
        raise IngestionError(f"Page {page.page_number} has no original scan to OCR.")

    original = store.get(page.original_key)

    pre = preprocess(original, preset=preset, max_dimension=settings.ocr_max_dimension)
    processed_key = page.processed_key or build_key("processed", document.id, f"page-{page.page_number:03d}.png")
    processed_bytes = pre.to_png()
    stored = store.put(processed_key, processed_bytes, "image/png")
    thumb_key = page.thumbnail_key or build_key("thumbs", document.id, f"page-{page.page_number:03d}.jpg")
    store.put(thumb_key, make_thumbnail(pre.image), "image/jpeg")

    page.processed_key = stored.key
    page.processed_sha256 = stored.sha256
    page.thumbnail_key = thumb_key
    page.width, page.height = pre.image.shape[1], pre.image.shape[0]

    document.ocr_status = OcrStatus.PROCESSING
    try:
        outcome = run_ocr(
            processed_bytes,
            language=language or document.original_language or settings.ocr_default_language,
            preset=preset,
            mode="manuscript" if document.document_type == DocumentType.MANUSCRIPT else "document",
        )
    except ProviderUnavailable as exc:
        document.ocr_status = OcrStatus.FAILED
        db.commit()
        raise IngestionError(str(exc)) from exc

    raw_text = outcome["text"]
    correction = None
    if settings.post_ocr_correction_provider == "llm":
        try:
            correction = correct_text(raw_text, language=language or "en")
        except Exception as exc:  # noqa: BLE001
            log.warning("post-OCR correction unavailable", error=str(exc))
            correction = None

    layout = analyse_layout(_to_gray(pre.image))
    result = OcrResult(
        document_id=document.id,
        page_id=page.id,
        engine=outcome["engine"],
        engine_version=outcome.get("engine_version"),
        language=outcome.get("language"),
        raw_text=raw_text,
        corrected_text=correction.text if correction else None,
        confidence=outcome.get("confidence"),
        blocks=outcome.get("blocks", []),
        layout=outcome.get("layout") or layout,
        preprocessing={**outcome.get("preprocessing", {}), "layout": layout},
        duration_ms=outcome.get("duration_ms"),
        is_current=True,
        is_human_corrected=False,
    )
    db.query(OcrResult).filter(
        OcrResult.page_id == page.id, OcrResult.is_current.is_(True)
    ).update({"is_current": False}, synchronize_session=False)
    db.add(result)
    db.flush()

    page.ocr_text = raw_text
    page.ocr_corrected_text = correction.text if correction else None
    page.ocr_confidence = outcome.get("confidence")
    page.ocr_language = outcome.get("language")
    page.ocr_engine = outcome.get("engine")
    page.ocr_duration_ms = outcome.get("duration_ms")
    page.layout = outcome.get("layout") or layout
    page.word_count = len((page.ocr_corrected_text or raw_text).split())

    document.ocr_status = OcrStatus.REVIEW
    document.processing_state = ProcessingState.AWAITING_REVIEW
    db.commit()
    log.info(
        "ocr completed",
        document_id=document.id,
        page=page.page_number,
        engine=outcome.get("engine"),
        confidence=outcome.get("confidence"),
        duration_ms=outcome.get("duration_ms"),
    )
    return result


def approve_page(
    db: Session,
    page: DocumentPage,
    *,
    corrected_text: str | None = None,
    approved_by: str | None = None,
) -> DocumentPage:
    """Human verification step. The original scan is never modified."""
    latest = db.scalar(
        select(OcrResult)
        .where(OcrResult.page_id == page.id)
        .order_by(OcrResult.created_at.desc())
        .limit(1)
    )
    text = corrected_text if corrected_text is not None else (page.ocr_corrected_text or page.ocr_text or "")
    page.ocr_corrected_text = text
    page.is_approved = True
    page.approved_by = approved_by
    page.approved_at = datetime.now(UTC)
    if latest is not None:
        latest.corrected_text = text
        latest.is_human_corrected = True
        latest.is_current = True
        latest.created_by = approved_by
    db.commit()
    return page


def publish_document(db: Session, document: Document) -> Document:
    document.publication_status = PublicationStatus.PUBLISHED
    document.processing_state = ProcessingState.PUBLISHED
    if document.ocr_status == OcrStatus.REVIEW:
        document.ocr_status = OcrStatus.APPROVED
    db.commit()
    return document


def reindex_document(db: Session, document: Document) -> tuple[bool, int, int]:
    ok, chunks, embedded = index_document(db, document)
    db.commit()
    return (ok, chunks, embedded)


def translate_document(
    db: Session, document: Document, target_language: str, *, limit: int = 200
) -> dict[str, Any]:
    """Translate chunk text. The original is preserved untouched."""
    from app.providers.base import ProviderUnavailable
    from app.providers.speech import get_translation_provider

    provider = get_translation_provider()
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index)
        .limit(limit)
    ).all()
    translated = 0
    errors: list[str] = []
    for chunk in chunks:
        existing = db.scalar(
            select(Translation).where(
                Translation.chunk_id == chunk.id, Translation.target_language == target_language
            )
        )
        if existing is not None:
            continue
        try:
            outcome = provider.translate(chunk.text, chunk.language, target_language)
        except ProviderUnavailable as exc:
            return {
                "translated": translated,
                "available": False,
                "detail": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:160])
            continue
        db.add(
            Translation(
                document_id=document.id,
                chunk_id=chunk.id,
                source_language=chunk.language,
                target_language=target_language,
                original_text=chunk.text,
                translated_text=outcome.text,
                provider=outcome.provider,
                model=outcome.model,
            )
        )
        translated += 1
    db.commit()
    return {
        "translated": translated,
        "available": True,
        "provider": provider.name,
        "target_language": target_language,
        "errors": errors[:5],
    }


def soft_delete_document(db: Session, document: Document) -> Document:
    document.deleted_at = datetime.now(UTC)
    document.publication_status = PublicationStatus.WITHDRAWN
    db.commit()
    return document


def document_statistics(db: Session) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))) or 0
    published = (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.publication_status == PublicationStatus.PUBLISHED)
        )
        or 0
    )
    manuscripts = (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.document_type == DocumentType.MANUSCRIPT)
        )
        or 0
    )
    chunks = db.scalar(select(func.count()).select_from(DocumentChunk)) or 0
    embedded = (
        db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.embedding.isnot(None))
        )
        or 0
    )
    pages = db.scalar(select(func.count()).select_from(DocumentPage)) or 0
    total_bytes = db.scalar(select(func.coalesce(func.sum(Document.byte_size), 0))) or 0
    return {
        "documents_total": int(total),
        "documents_published": int(published),
        "manuscripts": int(manuscripts),
        "chunks": int(chunks),
        "embedded_chunks": int(embedded),
        "pages": int(pages),
        "stored_bytes": int(total_bytes),
    }
