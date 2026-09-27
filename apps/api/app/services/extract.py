"""Text extraction from uploaded archival files.

Every extractor reports what it actually did. When a file is a scan (no text
layer) the result is marked ``requires_ocr=True`` so the pipeline routes it to
OCR rather than pretending to have read it.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.models.enums import DocumentType

log = get_logger(__name__)

EXTENSION_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".doc": "application/msword",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".jsonl"}

DOC_TYPE_BY_EXT: dict[str, DocumentType] = {
    ".pdf": DocumentType.ARCHIVAL_RECORD,
    ".doc": DocumentType.ARTICLE,
    ".docx": DocumentType.ARTICLE,
    ".txt": DocumentType.ARCHIVAL_RECORD,
    ".md": DocumentType.ARCHIVAL_RECORD,
    ".json": DocumentType.ARCHIVAL_RECORD,
    ".jsonl": DocumentType.ARCHIVAL_RECORD,
    ".csv": DocumentType.ARCHIVAL_RECORD,
}
for _ext in IMAGE_EXTENSIONS:
    DOC_TYPE_BY_EXT[_ext] = DocumentType.MANUSCRIPT
for _ext in AUDIO_EXTENSIONS:
    DOC_TYPE_BY_EXT[_ext] = DocumentType.AUDIO
for _ext in VIDEO_EXTENSIONS:
    DOC_TYPE_BY_EXT[_ext] = DocumentType.VIDEO


@dataclass(slots=True)
class ExtractedText:
    text: str
    pages: list[str] = field(default_factory=list)
    page_count: int = 0
    requires_ocr: bool = False
    extractor: str = "none"
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def guess_extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def guess_mime(filename: str) -> str:
    return EXTENSION_TYPES.get(guess_extension(filename), "application/octet-stream")


def classify_document_type(filename: str, explicit: str | None = None) -> DocumentType:
    if explicit:
        try:
            return DocumentType(explicit)
        except ValueError:
            log.warning("unknown document_type supplied, falling back to extension", value=explicit)
    return DOC_TYPE_BY_EXT.get(guess_extension(filename), DocumentType.ARCHIVAL_RECORD)


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace("", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


def extract_text(data: bytes, filename: str) -> ExtractedText:
    ext = guess_extension(filename)

    if ext in TEXT_EXTENSIONS:
        raw = data.decode("utf-8", errors="replace")
        if ext == ".jsonl":
            raw = _pretty_jsonl(raw)
        text = _clean(raw)
        return ExtractedText(
            text=text,
            pages=[text] if text else [],
            page_count=1 if text else 0,
            extractor=f"text:{ext.lstrip('.')}",
        )

    if ext == ".pdf":
        return _extract_pdf(data)

    if ext in (".docx", ".doc"):
        return _extract_docx(data)

    if ext in IMAGE_EXTENSIONS:
        return ExtractedText(
            text="",
            pages=[],
            page_count=1,
            requires_ocr=True,
            extractor="none",
            warnings=["Image file has no text layer; OCR is required."],
        )

    if ext in AUDIO_EXTENSIONS or ext in VIDEO_EXTENSIONS:
        return ExtractedText(
            text="",
            pages=[],
            page_count=0,
            requires_ocr=False,
            extractor="none",
            warnings=["Media file; transcription is a separate job."],
        )

    return ExtractedText(
        text="",
        pages=[],
        page_count=0,
        extractor="none",
        warnings=[f"No extractor for extension '{ext}'."],
    )


def _pretty_jsonl(raw: str) -> str:
    lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.dumps(json.loads(line), ensure_ascii=False, indent=2))
        except json.JSONDecodeError:
            lines.append(line)
    return "\n".join(lines)


def _extract_pdf(data: bytes) -> ExtractedText:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        return ExtractedText(
            text="", requires_ocr=True, extractor="none", warnings=[f"pypdf missing: {exc}"]
        )
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        log.warning("pdf parse failed", error=str(exc))
        return ExtractedText(
            text="",
            requires_ocr=True,
            extractor="none",
            warnings=[f"PDF could not be parsed: {exc}"],
        )

    pages: list[str] = []
    warnings: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"page {i + 1}: {exc}")
            text = ""
        pages.append(_clean(text))

    total_chars = sum(len(p) for p in pages)
    empty_pages = sum(1 for p in pages if not p)
    requires_ocr = total_chars < 40 or empty_pages > 0
    return ExtractedText(
        text="\n\n".join(p for p in pages if p),
        pages=pages,
        page_count=len(pages),
        requires_ocr=requires_ocr,
        extractor="pypdf",
        warnings=warnings
        + (
            [f"{empty_pages} page(s) have no text layer and need OCR."]
            if empty_pages
            else []
        ),
    )


def _extract_docx(data: bytes) -> ExtractedText:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        return ExtractedText(text="", extractor="none", warnings=[f"python-docx missing: {exc}"])
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        warnings = [f"DOCX could not be parsed: {exc}"]
        if not zipfile.is_zipfile(io.BytesIO(data)):
            warnings.append(
                "Legacy .doc (OLE2) format is not parsed. Convert to .docx or PDF, or run OCR "
                "on a rendered copy."
            )
        return ExtractedText(text="", extractor="none", warnings=warnings)
    paragraphs = [p.text for p in document.paragraphs]
    return ExtractedText(
        text=_clean("\n".join(paragraphs)),
        pages=[_clean("\n".join(paragraphs))],
        page_count=1,
        extractor="python-docx",
    )


def csv_rows_to_text(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return "\n".join(" | ".join(row) for row in reader)
