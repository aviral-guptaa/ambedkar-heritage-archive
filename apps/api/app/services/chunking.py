"""Structural / semantic chunking.

The chunker is paragraph- and sentence-aware. It prefers to break at
structural boundaries (headings, list items, numbered clauses) and falls back to
a sentence boundary, so a retrieved passage rarely starts or ends mid-clause.
Every chunk keeps an exact ``char_start``/``char_end`` offset and the page number
it came from, which is what makes page-level citations trustworthy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.core.config import settings

HEADING_RE = re.compile(
    r"^\s{0,6}(?:#{1,6}\s+|[IVXLC]+\.\s+|\d{1,3}(?:\.\d{1,3})*\s+[A-Z]|[A-Z][A-Z\s,&'()\-]{6,}:?)\s*$"
)
BULLET_RE = re.compile(r"^\s{0,8}([-*•–—]|\d+[.)])\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?।॥])\s+(?=[A-Z\u0900-\u097F\"'“(])")
_WS_RE = re.compile(r"[ \t]+")


@dataclass(slots=True)
class Chunk:
    text: str
    index: int
    section: str | None = None
    page_number: int | None = None
    char_start: int = 0
    char_end: int = 0
    language: str = "en"
    kind: str = "text"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.text) // 4)


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def detect_language(text: str) -> str:
    """Script-based language detection for the languages the archive indexes."""
    sample = text[:4000]
    if not sample.strip():
        return "en"
    devanagari = sum(1 for ch in sample if "ऀ" <= ch <= "ॿ")
    latin = sum(1 for ch in sample if ("a" <= ch.lower() <= "z"))
    total = devanagari + latin
    if total == 0:
        return "en"
    if devanagari / total > 0.25:
        # Distinguish Hindi from Marathi by a small, explicit marker set.
        marathi_markers = (
            "आहे",
            "आणि",
            "मध्ये",
            "करण्यात",
            "आहेत",
            "नाही",
            "पण",
            "यांनी",
            "काय",
            "म्हणून",
        )
        hits = sum(1 for m in marathi_markers if m in sample)
        return "mr" if hits >= 2 else "hi"
    return "en"


def split_paragraphs(text: str) -> list[tuple[int, str]]:
    """Return (offset, paragraph) pairs preserving absolute offsets."""
    out: list[tuple[int, str]] = []
    offset = 0
    for para in text.split("\n\n"):
        stripped = para.strip()
        if stripped:
            start = text.find(para, offset)
            if start < 0:
                start = offset
            lead = len(para) - len(para.lstrip())
            out.append((start + lead, stripped))
        offset += len(para) + 2
    return out


def _split_sentences(paragraph: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_RE.split(paragraph) if p.strip()]
    return parts or ([paragraph.strip()] if paragraph.strip() else [])


def _overlap_text(tail: str, overlap: int) -> str:
    """The trailing sentences of ``tail`` that fit inside the overlap window.

    Cutting the window at an arbitrary character would make every overlapping
    chunk open in the middle of a word. A citation that starts "ass in ancient
    Indian society" reads as corruption, so the overlap is taken from sentence
    boundaries instead.
    """
    if len(tail) <= overlap:
        return tail
    kept = ""
    for sentence in reversed(_split_sentences(tail)):
        candidate = f"{sentence} {kept}".strip() if kept else sentence
        if len(candidate) > overlap:
            break
        kept = candidate
    if kept:
        return kept
    # A single sentence longer than the window: back up to a word boundary so
    # the chunk still starts on a whole word.
    return tail[-(overlap + 1) :].lstrip(" ,;:-")


def _is_heading(paragraph: str) -> bool:
    if len(paragraph) > 120:
        return False
    if paragraph.startswith("#"):
        return True
    if HEADING_RE.match(paragraph):
        return True
    words = paragraph.split()
    if 1 <= len(words) <= 10 and sum(w.isupper() for w in words if w.isalpha()) >= max(
        1, len([w for w in words if w.isalpha()]) - 1
    ):
        return paragraph.isupper() and len(paragraph) < 90
    return False


def chunk_pages(
    pages: Iterable[tuple[int, str]],
    *,
    target_chars: int | None = None,
    overlap_chars: int | None = None,
    min_chars: int | None = None,
) -> list[Chunk]:
    """Chunk ``(page_number, text)`` pairs into retrieval units.

    ``page_number`` is 1-based. Pass ``0`` or ``None`` for documents without
    pagination (the text is then treated as a single page).
    """
    target = target_chars or settings.chunk_target_chars
    overlap = overlap_chars if overlap_chars is not None else settings.chunk_overlap_chars
    minimum = min_chars if min_chars is not None else settings.chunk_min_chars

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_len = 0
    section: str | None = None
    page_number: int | None = None
    start_offset = 0
    cursor = 0
    index = 0

    def flush() -> None:
        nonlocal buffer, buffer_len, index
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        if text:
            chunks.append(
                Chunk(
                    text=text,
                    index=index,
                    section=section,
                    page_number=page_number,
                    char_start=start_offset,
                    char_end=start_offset + len(text),
                    language=detect_language(text),
                )
            )
            index += 1
        if overlap and buffer:
            kept = _overlap_text(buffer[-1], overlap)
            buffer = [kept] if kept.strip() else []
            buffer_len = sum(len(b) for b in buffer)
        else:
            buffer = []
            buffer_len = 0

    for raw_page, page_text in pages:
        if page_number is None:
            page_number = raw_page or None
        if not page_text.strip():
            continue
        for offset, paragraph in split_paragraphs(page_text):
            if _is_heading(paragraph):
                flush()
                section = paragraph.strip().lstrip("#").strip()
                page_number = page_number if page_number is not None else (raw_page or None)
                start_offset = cursor
                buffer = []
                buffer_len = 0
                cursor += len(paragraph) + 2
                continue

            sentences = _split_sentences(paragraph)
            for sentence in sentences:
                if buffer_len and buffer_len + len(sentence) + 1 > target:
                    flush()
                    start_offset = cursor
                    page_number = page_number if page_number is not None else (raw_page or None)
                if not buffer:
                    start_offset = offset
                buffer.append(sentence)
                buffer_len += len(sentence) + 1
                cursor += len(sentence) + 1
            cursor += 2
        if buffer_len >= minimum:
            flush()
            start_offset = cursor
    flush()

    # Merge runt chunks that sit right below the minimum into their predecessor.
    # Never merge across a section boundary: the merged chunk can only carry one
    # section label, so merging would silently attribute text to the wrong
    # section and the citation would name the wrong heading.
    merged: list[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and len(chunk.text) < minimum
            and chunk.page_number == merged[-1].page_number
            and chunk.section == merged[-1].section
            and len(merged[-1].text) + len(chunk.text) < target * 1.4
        ):
            merged[-1].text = f"{merged[-1].text}\n{chunk.text}"
            merged[-1].char_end = chunk.char_end
            continue
        merged.append(chunk)
    for i, chunk in enumerate(merged):
        chunk.index = i
    return merged


def chunk_document(text: str, *, page_count: int = 1) -> list[Chunk]:
    """Convenience wrapper for documents ingested as one continuous text."""
    if page_count > 1 and "\f" in text:
        pages = []
        for i, page in enumerate(text.split("\f"), start=1):
            pages.append((i, page))
        return chunk_pages(pages)
    return chunk_pages([(1, text)])


def chunk_with_language(text: str, language: str | None = None) -> list[Chunk]:
    chunks = chunk_document(text)
    if language:
        for chunk in chunks:
            chunk.language = language
    return chunks
