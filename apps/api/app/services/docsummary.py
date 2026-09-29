"""Grounded 30-second understanding of a single archival record.

The rules this module exists to enforce:

* every sentence in the output is copied from the document's own indexed text;
* no model, prompt, or world knowledge is consulted, so there is nothing to
  hallucinate and nothing to cite that the archive does not hold;
* the document's own metadata may only describe the record's form (what kind of
  thing it is, when, in which venue), never its historical content;
* when the record has too little text to summarise, the module says so instead
  of padding the answer out.

Because nothing is generated, the same request always returns the same summary
and the free deployment needs no API key to serve it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.archive import Document, DocumentChunk
from app.providers.llm import split_sentences

#: A record with fewer characters than this cannot support a real summary, and
#: a reader is better served by an honest "not enough text" than by filler.
MIN_TEXT_CHARS = 400

#: Sentences outside this band are headers, page furniture, or fragments.
MIN_SENTENCE_CHARS = 45
MAX_SENTENCE_CHARS = 340

#: More than this many candidates and the extra text stops being a 30-second read.
MAX_CANDIDATES = 400

#: Sentences whose content words overlap by more than this are near-duplicates,
#: which is common in scans that repeat a heading on every page.
DUPLICATE_OVERLAP = 0.6

#: Reserved for the reader's question, so a summary never answers a question
#: nobody asked.
_SUMMARY_QUESTION = "What is this record, and what does it say?"


@dataclass(slots=True)
class GroundedSentence:
    """One real sentence from the record, with the pointer needed to cite it."""

    text: str
    page_number: int | None
    chunk_index: int
    position: int

    def as_citation(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "chunk_index": self.chunk_index,
            "quote": self.text,
        }


def _content_tokens(text: str) -> set[str]:
    """Lowercase word tokens, including Devanagari, ignoring pure punctuation."""
    import re

    return {t for t in re.findall(r"[\w\u0900-\u097F]+", text.lower(), re.UNICODE) if len(t) > 2}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _type_phrase(document_type: str | None) -> str:
    """A neutral phrase for the record's form. Describes the object, not history."""
    return {
        "speech": "A speech",
        "article": "An article",
        "book": "A book",
        "archival_record": "An archival record",
        "manuscript": "A manuscript",
        "report": "A report",
        "letter": "A letter",
        "audio": "An audio recording",
        "video": "A video recording",
    }.get((document_type or "").lower(), "An archival record")


def what_is_this(document: Document) -> str:
    """One sentence about what the record *is*, built from its own metadata.

    This is cataloguing, not interpretation: kind, year, venue, and language are
    facts the archive recorded about the object when it was catalogued.
    """
    phrase = _type_phrase(document.document_type)
    parts: list[str] = []
    if document.year:
        parts.append(f"from {document.year}")
    venue = (document.venue or "").strip()
    if venue:
        parts.append(f"delivered at {venue}")
    elif document.year_from or document.year_to:
        span = f"{document.year_from or '?'}-{document.year_to or '?'}"
        parts.append(f"dated {span}")
    tail = f" ({', '.join(parts)})" if parts else ""
    language = (document.language or "").lower()
    lang_note = " The record is catalogued in English." if language == "en" else ""
    return f"{phrase} held by the archive{tail}.{lang_note}"


def _lead_sentences(document: Document, chunks: list[DocumentChunk]) -> list[GroundedSentence]:
    """Candidate sentences in reading order, one per page where possible.

    A record's opening pages are the most reliable place to find what a text is
    about, so the scan walks forward and takes the first usable sentence from
    each page before moving on.
    """
    by_page: dict[int, DocumentChunk] = {}
    for chunk in chunks:
        key = chunk.page_number if chunk.page_number is not None else chunk.chunk_index
        by_page.setdefault(key, chunk)

    out: list[GroundedSentence] = []
    position = 0
    for chunk in by_page.values():
        for sentence in split_sentences(chunk.text or ""):
            sentence = sentence.strip()
            if not MIN_SENTENCE_CHARS <= len(sentence) <= MAX_SENTENCE_CHARS:
                continue
            if sentence.endswith((":", ";")):
                continue
            out.append(
                GroundedSentence(
                    text=sentence,
                    page_number=chunk.page_number,
                    chunk_index=chunk.chunk_index,
                    position=position,
                )
            )
            position += 1
            if len(out) >= MAX_CANDIDATES:
                return out
    return out


def _score(candidates: list[GroundedSentence], document: Document) -> list[GroundedSentence]:
    """Rank candidates using only in-document signals.

    The scoring mirrors the extractive answer composer: coverage of the
    document's own frequent terms, a mild preference for earlier text, and a
    length band that avoids headers and run-on passages.
    """
    from collections import Counter

    from app.providers.llm import ExtractiveGroundedProvider

    freq: Counter[str] = Counter()
    for candidate in candidates:
        freq.update(_content_tokens(candidate.text))

    # Terms the record repeats are its own vocabulary; that is the only signal
    # available without a model, and it is drawn from the record alone.
    title_tokens = _content_tokens(f"{document.title or ''} {document.subtitle or ''}")
    common = {term for term, count in freq.items() if count >= 3 and term not in title_tokens}

    provider = ExtractiveGroundedProvider()
    base_scores = provider._sentence_scores(_SUMMARY_QUESTION, [c.text for c in candidates])

    total = max(1, len(candidates))
    scored: list[tuple[float, GroundedSentence]] = []
    for index, (candidate, base) in enumerate(zip(candidates, base_scores)):
        tokens = _content_tokens(candidate.text)
        if not tokens:
            continue
        # How much of the sentence is drawn from the record's own repeated
        # vocabulary, and how early it appears. Both are document-internal.
        centrality = sum(1 for t in tokens if t in common) / len(tokens)
        earliness = 1.0 - (index / total)
        score = base * 0.4 + centrality * 0.45 + earliness * 0.15
        scored.append((score, candidate))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [candidate for _score, candidate in scored]


def _dedupe(chosen: list[GroundedSentence]) -> list[GroundedSentence]:
    """Drop near-duplicate sentences, which scans produce constantly."""
    kept: list[GroundedSentence] = []
    seen: list[set[str]] = []
    for candidate in chosen:
        tokens = _content_tokens(candidate.text)
        if any(_overlap(tokens, prior) > DUPLICATE_OVERLAP for prior in seen):
            continue
        seen.append(tokens)
        kept.append(candidate)
    return kept


def load_chunks(db: Session, document: Document, *, limit: int = 60) -> list[DocumentChunk]:
    """The record's own indexed text, in reading order."""
    return list(
        db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
            .limit(limit)
        ).all()
    )


def build_summary(db: Session, document: Document) -> dict[str, Any]:
    """Build the 30-second understanding of one record from its own text."""
    chunks = load_chunks(db, document)
    text_chars = sum(len(c.text or "") for c in chunks)

    base: dict[str, Any] = {
        "document_id": document.id,
        "document_slug": document.slug,
        "available": False,
        "disclosure": (
            "AI-generated summary based on this archival record. Every line is "
            "quoted from the record's own text."
        ),
        "method": "extractive",
        "model_id": None,
        "what_is_this": what_is_this(document),
        "main_idea": None,
        "key_points": [],
        "source_characters": text_chars,
        "source_chunks": len(chunks),
        "unavailable_reason": None,
        "citations": [],
    }

    if text_chars < MIN_TEXT_CHARS:
        base["unavailable_reason"] = (
            f"This record has only {text_chars} characters of indexed text, which is "
            "not enough to summarise honestly. The full text is available to read."
        )
        return base

    candidates = _lead_sentences(document, chunks)
    if not candidates:
        base["unavailable_reason"] = (
            "This record's indexed text has no complete sentences to quote, so no "
            "summary could be produced without inventing content."
        )
        return base

    ranked = _score(candidates, document)
    if not ranked:
        base["unavailable_reason"] = "No usable sentence could be selected from this record."
        return base

    top = _dedupe(ranked)[:4]
    if not top:
        base["unavailable_reason"] = "No usable sentence could be selected from this record."
        return base

    # The main idea is the strongest sentence; key points follow, dropping it.
    key_points = [s for s in top[1:]][:3]

    base["available"] = True
    base["main_idea"] = top[0].text
    base["key_points"] = [
        {
            "text": sentence.text,
            "page_number": sentence.page_number,
            "chunk_index": sentence.chunk_index,
        }
        for sentence in key_points
    ]
    base["citations"] = [
        {"text": sentence.text, "page_number": sentence.page_number}
        for sentence in top
    ]
    return base
