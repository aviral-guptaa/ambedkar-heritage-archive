"""OAIS-inspired digital preservation.

* **SIP** (Submission Information Package) — what an archivist uploads.
* **AIP** (Archival Information Package) — the immutable, checksummed version
  written to preservation storage, never overwritten.
* **DIP** (Dissemination Information Package) — the derived, publishable
  artefacts: processed page images, OCR text, thumbnails, transcripts.

Every stored file gets a SHA-256 recorded at ingest. ``verify_document`` recomputes
it and writes an ``integrity_checks`` row. Mismatches are surfaced, never
auto-repaired, because a silent overwrite would destroy the audit trail.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.archive import Document, DocumentPage, DocumentVersion
from app.models.ops import IntegrityCheck
from app.providers.storage import get_object_store, sha256_bytes

log = get_logger(__name__)

HASH_CHUNK = 1024 * 1024

STATUS_VERIFIED = "verified"
STATUS_MISMATCH = "mismatch"
STATUS_MISSING = "missing"


@dataclass(slots=True)
class IntegrityReport:
    scope: str
    object_key: str
    original_sha256: str
    current_sha256: str | None
    status: str
    byte_size: int | None
    checked_at: datetime
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == STATUS_VERIFIED

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "object_key": self.object_key,
            "original_sha256": self.original_sha256,
            "current_sha256": self.current_sha256,
            "status": self.status,
            "ok": self.ok,
            "byte_size": self.byte_size,
            "checked_at": self.checked_at.isoformat(),
            "detail": self.detail,
        }


def hash_stream(fileobj) -> str:  # noqa: ANN001
    digest = hashlib.sha256()
    for block in iter(lambda: fileobj.read(HASH_CHUNK), b""):
        digest.update(block)
    return digest.hexdigest()


def verify_object(object_key: str, expected_sha256: str) -> IntegrityReport:
    store = get_object_store()
    now = datetime.now(UTC)
    stat = store.stat(object_key)
    if stat is None:
        return IntegrityReport(
            scope="object",
            object_key=object_key,
            original_sha256=expected_sha256,
            current_sha256=None,
            status=STATUS_MISSING,
            byte_size=None,
            checked_at=now,
            detail="Object not found in preservation storage.",
        )
    current = stat.sha256 or ""
    if not current:
        with store.open_stream(object_key) as fh:
            current = hash_stream(fh)
    if current == expected_sha256:
        return IntegrityReport(
            scope="object",
            object_key=object_key,
            original_sha256=expected_sha256,
            current_sha256=current,
            status=STATUS_VERIFIED,
            byte_size=stat.size,
            checked_at=now,
            detail="Checksum matches the value recorded at ingest.",
        )
    return IntegrityReport(
        scope="object",
        object_key=object_key,
        original_sha256=expected_sha256,
        current_sha256=current,
        status=STATUS_MISMATCH,
        byte_size=stat.size,
        checked_at=now,
        detail="Checksum differs from the value recorded at ingest. The stored file has been "
        "altered since preservation.",
    )


def record_version(
    db: Session,
    document: Document,
    *,
    data: bytes,
    storage_key: str,
    mime_type: str | None,
    note: str | None = None,
    created_by: str | None = None,
) -> DocumentVersion:
    digest = sha256_bytes(data)
    version = (document.version or 0) + 1
    db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document.id, DocumentVersion.is_current.is_(True)
    ).update({"is_current": False}, synchronize_session=False)
    record = DocumentVersion(
        document_id=document.id,
        version=version,
        checksum_sha256=digest,
        storage_key=storage_key,
        byte_size=len(data),
        mime_type=mime_type,
        note=note,
        is_current=True,
        created_by=created_by,
    )
    db.add(record)
    document.version = version
    document.checksum_sha256 = digest
    document.byte_size = len(data)
    document.mime_type = mime_type
    document.storage_key = storage_key
    return record


def verify_document(db: Session, document: Document, *, persist: bool = True) -> list[IntegrityReport]:
    """Verify every preservation object that belongs to a document."""
    reports: list[IntegrityReport] = []

    targets: list[tuple[str, str, str]] = []
    if document.storage_key and document.checksum_sha256:
        targets.append(("original", document.storage_key, document.checksum_sha256))
    versions = db.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id, DocumentVersion.is_current.is_(True)
        )
    ).all()
    for version in versions:
        targets.append((f"version:{version.version}", version.storage_key, version.checksum_sha256))
    pages = db.scalars(select(DocumentPage).where(DocumentPage.document_id == document.id)).all()
    for page in pages:
        if page.original_key and page.original_sha256:
            targets.append((f"page:{page.page_number}", page.original_key, page.original_sha256))

    for scope, key, expected in targets:
        report = verify_object(key, expected)
        report.scope = scope
        reports.append(report)
        if persist:
            db.add(
                IntegrityCheck(
                    document_id=document.id,
                    scope=scope,
                    object_key=key,
                    original_sha256=expected,
                    current_sha256=report.current_sha256 or "",
                    status=report.status,
                    byte_size=report.byte_size,
                )
            )
    if persist:
        db.commit()
    return reports


def archive_integrity_summary(db: Session, *, verify: bool = False) -> dict[str, Any]:
    """Collection-wide integrity state, computed from real rows.

    With ``verify=True`` every preserved object is re-hashed first, so the
    numbers describe the bytes on disk now rather than what was true at ingest.
    """
    reports: list[dict[str, Any]] = []
    if verify:
        documents = db.scalars(
            select(Document).where(
                Document.deleted_at.is_(None), Document.storage_key.isnot(None)
            )
        ).all()
        for document in documents:
            for report in verify_document(db, document, persist=True):
                reports.append(
                    {
                        "document_id": document.id,
                        "object_key": report.object_key,
                        "scope": report.scope,
                        "expected_sha256": report.expected_sha256,
                        "actual_sha256": report.current_sha256,
                        "ok": report.ok,
                        "size": report.byte_size,
                        "checked_at": datetime.now(UTC).isoformat(),
                        "detail": report.detail,
                    }
                )

    latest: dict[str, IntegrityCheck] = {}
    rows = db.scalars(
        select(IntegrityCheck).order_by(IntegrityCheck.checked_at.asc())
    ).all()
    for row in rows:
        latest[f"{row.document_id}:{row.object_key}"] = row

    verified = sum(1 for r in latest.values() if r.status == STATUS_VERIFIED)
    mismatch = sum(1 for r in latest.values() if r.status == STATUS_MISMATCH)
    missing = sum(1 for r in latest.values() if r.status == STATUS_MISSING)
    total = len(latest)
    percent = round(100.0 * verified / total, 2) if total else None

    documents_with_objects = {
        key.split(":", 1)[0] for key in latest if key.split(":", 1)[0] != "None"
    }
    total_documents = (
        db.scalar(
            select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
        )
        or 0
    )
    result: dict[str, Any] = {
        "checked_objects": total,
        "verified": verified,
        "mismatch": mismatch,
        "missing": missing,
        "verified_percent": percent,
        "documents_with_checks": len(documents_with_objects),
        "documents_total": int(total_documents),
        "documents_never_checked": int(total_documents) - len(documents_with_objects),
        "status": (
            "not_yet_verified"
            if total == 0
            else ("verified" if mismatch == 0 and missing == 0 else "attention_required")
        ),
    }
    if reports:
        result["reports"] = reports
    return result


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_length: int = 80) -> str:
    slug = _SLUG_STRIP.sub("-", (value or "").lower()).strip("-")
    return (slug[:max_length].rstrip("-")) or "untitled"
