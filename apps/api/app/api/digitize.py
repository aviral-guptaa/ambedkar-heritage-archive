"""Public digitisation: upload a manuscript, OCR it, report translation honestly.

Design constraints, in order of importance:

1. **The original is never modified.** The uploaded bytes are stored under a
   preservation key, and every later step reads from that stored original.
2. **OCR is real or it is reported as failed.** This module never fabricates
   text, and never presents a stored fixture as if it were the result of reading
   the visitor's file.
3. **Translation is reported truthfully.** With no translation provider
   configured the API says so and returns the original-language text unchanged,
   rather than returning English text that nothing produced.
4. **Uploads create drafts, never published records.** An anonymous upload can
   never alter the published archive; it lands as a private draft the uploader
   alone can read.

No model is involved anywhere in this path, so the whole flow works on the free
deployment with OCR as its only external requirement.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentPrincipal, DbSession
from app.models.archive import Document, DocumentPage, OcrResult
from app.models.enums import (
    DocumentType,
    ProcessingState,
    PublicationStatus,
    VerificationStatus,
)
from app.providers.base import ProviderUnavailable
from app.providers.speech import get_translation_provider
from app.providers.storage import build_key, get_object_store
from app.services import extract as extract_mod
from app.services.ingestion import (
    IngestionError,
    record_version,
    run_ocr_stage,
    validate_upload,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/digitize", tags=["digitize"])

#: The digitise surface is for scans, so the accepted set is deliberately
#: narrower than the admin upload set (no audio or video belongs here).
DIGITIZE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}

#: Above this, OCR on a shared instance is not going to finish, and a visitor
#: should be told to try a smaller page rather than watch a request hang.
MAX_PAGES = 20

#: OCR is CPU-bound and runs inline in the request, so the accepted payload is
#: capped well below the admin upload limit. A 32 MB cap matches the deployed
#: configuration; one page of a 600 dpi scan is typically well under it.
MAX_DIGITIZE_BYTES = 32 * 1024 * 1024


class DigitizeCapabilities(BaseModel):
    """What this deployment can actually do, for the UI to show up front."""

    ocr_available: bool
    ocr_detail: str
    translation_available: bool
    translation_detail: str
    accepted_extensions: list[str]
    max_upload_bytes: int
    max_pages: int
    languages: list[str]


class DigitizeResult(BaseModel):
    """The honest outcome of one digitisation attempt."""

    document_id: str
    document_slug: str
    title: str
    filename: str
    sha256: str
    page_count: int
    ocr_status: str
    ocr_engine: str | None = None
    confidence: float | None = None
    detected_language: str | None = None
    #: Text as OCR read it, in the script of the original. Always present when
    #: OCR succeeded, even if translation did not.
    original_text: str = ""
    #: English text, only ever filled from a real translation provider.
    english_text: str | None = None
    translation_status: str
    translation_detail: str | None = None
    warnings: list[str] = Field(default_factory=list)
    #: True when the upload produced a private draft, never a published record.
    is_draft: bool = True
    disclosure: str = (
        "Text was read from your upload by OCR and may contain errors. "
        "Review it against the original before citing it."
    )


@router.get("/capabilities", response_model=DigitizeCapabilities, summary="What digitisation supports")
def capabilities() -> DigitizeCapabilities:
    """Report real provider state so the page never promises more than it can do."""
    from app.providers.ocr import get_ocr_provider

    ocr = get_ocr_provider()
    ocr_ok, ocr_detail = True, ""
    if hasattr(ocr, "health"):
        ocr_ok, ocr_detail = ocr.health()  # type: ignore[attr-defined]
    else:
        ocr_ok = ocr.is_available()
        if not ocr_ok:
            ocr_detail = "The OCR engine is not available on this deployment."

    translation = get_translation_provider()
    translation_ok = translation.is_available()
    if translation_ok:
        translation_detail = "Translation is available."
    else:
        translation_detail = (
            "Translation is not configured on this deployment, so the OCR text is "
            "returned in its original language. The original is always preserved and "
            "served unchanged."
        )

    return DigitizeCapabilities(
        ocr_available=ocr_ok,
        ocr_detail=ocr_detail,
        translation_available=translation_ok,
        translation_detail=translation_detail,
        accepted_extensions=sorted(DIGITIZE_EXTENSIONS),
        max_upload_bytes=min(settings.max_upload_bytes, MAX_DIGITIZE_BYTES),
        max_pages=MAX_PAGES,
        languages=list(settings.ocr_languages),
    )


@router.post("", response_model=DigitizeResult, summary="Digitise a manuscript")
def digitize(
    db: DbSession,
    principal: CurrentPrincipal,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    source_language: str | None = Form(default=None),
) -> DigitizeResult:
    """OCR an uploaded manuscript and report the result truthfully."""
    from app.providers.ocr import get_ocr_provider

    data = file.file.read()
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A filename is required."
        )

    try:
        meta = validate_upload(filename, data)
    except IngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if meta["extension"] not in DIGITIZE_EXTENSIONS:
        accepted = ", ".join(sorted(DIGITIZE_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Digitising accepts {accepted}. Received '{meta['extension']}'.",
        )

    limit = min(settings.max_upload_bytes, MAX_DIGITIZE_BYTES)
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds the {limit // (1024 * 1024)} MB digitisation limit. "
                "Nothing was uploaded or stored."
            ),
        )

    ocr = get_ocr_provider()
    if not ocr.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "OCR is not available on this deployment, so no text was read from your "
                "file. Your upload was not stored."
            ),
        )

    warnings: list[str] = []
    if meta.get("warning"):
        warnings.append(meta["warning"])

    # From here on the bytes live in preservation storage and are never written to.
    document = _create_draft(
        db,
        filename=filename,
        data=data,
        title=(title or "").strip() or f"Untitled manuscript ({filename})",
        source_language=source_language,
        principal=principal,
    )
    _create_page(db, document)

    ocr_result = _run_ocr(db, document, source_language)
    english_text, translation_status, translation_detail = _translate(
        ocr_result.corrected_text or ocr_result.raw_text or "",
        document.original_language or document.language or "unknown",
    )

    db.commit()

    return DigitizeResult(
        document_id=document.id,
        document_slug=document.slug,
        title=document.title,
        filename=meta["filename"],
        sha256=meta["sha256"],
        page_count=document.page_count or 1,
        ocr_status=str(document.ocr_status),
        ocr_engine=ocr_result.engine,
        confidence=ocr_result.confidence,
        detected_language=ocr_result.language,
        original_text=ocr_result.corrected_text or ocr_result.raw_text or "",
        english_text=english_text,
        translation_status=translation_status,
        translation_detail=translation_detail,
        warnings=warnings,
        is_draft=document.publication_status != "published",
    )


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #


def _create_draft(
    db,
    *,
    filename: str,
    title: str,
    source_language: str | None,
    principal,
    data: bytes,
) -> Document:
    """Create a private draft. An anonymous upload never becomes a published row."""
    import uuid

    from app.services.preservation import slugify

    resolved_language = source_language or settings.ocr_default_language
    document = Document(
        slug=f"digitized-{slugify(title)[:60]}-{uuid.uuid4().hex[:8]}",
        title=title,
        document_type=DocumentType.MANUSCRIPT.value,
        language=resolved_language,
        original_language=source_language,
        # A draft is invisible to search and to the public document list, so an
        # anonymous upload can never alter the published archive.
        publication_status=PublicationStatus.DRAFT,
        processing_state=ProcessingState.VALIDATED,
        # OCR text read from a visitor's own scan has not been checked by an
        # archivist, so it is pending review, never primary.
        verification_status=VerificationStatus.PENDING_REVIEW,
        is_demo=False,
        provenance=(
            "Uploaded through the public Digitise page. OCR text is unverified and must be "
            "checked against the original before publication."
        ),
        editorial_note=(
            "Public digitisation draft. Not published; original preserved unchanged."
        ),
    )
    db.add(document)
    db.flush()
    created_by = principal.id if principal.is_authenticated else None
    if created_by:
        document.created_by = created_by
    stored = _store_original(db, document, filename, data, created_by=created_by)
    document.storage_key = stored.key
    db.flush()
    return document


def _store_original(db, document: Document, filename: str, data: bytes, *, created_by: str | None):
    """Persist the uploaded bytes exactly as received, and record the version.

    Nothing downstream writes to this object, so the original a visitor uploaded
    is byte-for-byte what they sent.
    """
    store = get_object_store()
    key = build_key("originals", document.id, filename)
    stored = store.put(key, data)
    record_version(
        db,
        document,
        data=data,
        storage_key=stored.key,
        mime_type=None,
        note="Original upload (SIP promoted to AIP); never modified downstream",
        created_by=created_by,
    )
    document.checksum_sha256 = stored.sha256
    document.byte_size = stored.size
    db.flush()
    return stored


def _create_page(db, document: Document) -> DocumentPage:
    """Point a page record at the preserved original, so OCR reads the original."""
    page = DocumentPage(
        document_id=document.id,
        page_number=1,
        original_key=document.storage_key or None,
        original_sha256=document.checksum_sha256 or None,
    )
    db.add(page)
    db.flush()
    document.page_count = 1
    return page


def _run_ocr(db, document: Document, source_language: str | None) -> OcrResult:
    """Read the stored original with the OCR provider, and record the truth.

    Failures are stored as a failed OCR status on the document rather than being
    turned into plausible-looking text.
    """
    from app.models.enums import OcrStatus

    page = db.scalar(
        select(DocumentPage).where(DocumentPage.document_id == document.id).limit(1)
    )
    if page is None:  # pragma: no cover - _create_page guarantees one
        document.ocr_status = OcrStatus.FAILED
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The upload could not be prepared for OCR.",
        )

    try:
        result = run_ocr_stage(
            db,
            document,
            page,
            language=source_language or settings.ocr_default_language,
            preset="manuscript",
        )
    except ProviderUnavailable as exc:
        document.ocr_status = OcrStatus.FAILED
        db.flush()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"OCR could not run on this deployment: {exc} No text was read from your file."
            ),
        ) from exc
    except IngestionError as exc:
        document.ocr_status = OcrStatus.FAILED
        db.flush()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The file could not be prepared for OCR: {exc} No text was read.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        # An unreadable scan must be reported as unreadable, never as empty text
        # that a reader could mistake for a blank page.
        document.ocr_status = OcrStatus.FAILED
        db.flush()
        log.warning("digitize ocr failed document_id=%s error=%s", document.id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "OCR could not read this file, so no text was produced. The original "
                "is unchanged. A clearer scan, or a PDF, may work."
            ),
        ) from exc

    document.ocr_status = OcrStatus.REVIEW if result.raw_text.strip() else OcrStatus.FAILED
    if source_language:
        document.language = source_language
    return result


def _translate(text: str, source_language: str) -> tuple[str | None, str, str | None]:
    """Translate when a provider is genuinely configured; otherwise say so.

    Returns ``(english_text, status, detail)``. ``english_text`` is only ever
    non-``None`` when a real provider produced it.
    """
    if not text.strip():
        return None, "not_attempted", "No OCR text was available to translate."

    provider = get_translation_provider()
    if not provider.is_available():
        return (
            None,
            "unavailable",
            "Translation is not configured on this deployment, so the text is shown as OCR "
            "read it, in the original language. Nothing was translated or invented.",
        )

    try:
        outcome = provider.translate(text, source_language, "en")
    except ProviderUnavailable as exc:
        return None, "unavailable", str(exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("digitize translation failed error=%s", exc)
        return None, "failed", f"Translation failed: {exc}"

    translated = getattr(outcome, "text", "") or ""
    if not translated.strip():
        return None, "failed", "The translation provider returned no text."
    return translated, "complete", None
