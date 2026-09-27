"""Import the Ambedkar speech catalogue into the archive.

    python -m scripts.import_corpus --path /path/to/speeches.jsonl --publish

What this script does and, more importantly, does not claim
------------------------------------------------------------
The input is a derived dataset. Its bibliographic fields (title, date, venue,
cited URL, rights) are usable. Its ``transcript`` field is *not* a verified
transcription: every record is roughly 1,000 characters, 519 of them embed a
machine-written ``## TL;DR``, and the section headings are modern editorial
titles. The real 1916 "Castes in India" alone runs to tens of thousands of words.

So each record is imported as:

* a **catalogue entry** with full provenance (source, URL, rights, digest), and
* an **unverified secondary text**, stored with its raw corpus bytes preserved
  and the original sha256 recorded.

Every chunk is written with ``quote_verified = false`` and the document with
``verification_status = 'unverified_secondary'``. The RAG layer refuses to put
this text in quotation marks or attribute it to Ambedkar as verbatim speech; it
is surfaced as a summary that always links back to the cited source.

To promote a record to ``verified_primary`` an archivist must fetch the cited
URL, compare it, and record the comparison — see ``scripts/verify_source.py``.
Nothing in this script does that, because it cannot.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session


# Allow "python scripts/<name>.py" from anywhere, which is how the README
# documents these commands, without requiring PYTHONPATH to be set first.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from app.core.logging import configure_logging, get_logger
from app.db.base import session_scope
from app.models.archive import Document
from app.models.enums import DocumentType, ProcessingState, VerificationStatus
from app.models.knowledge import Event
from app.models.archive import Collection
from app.services.ingestion import (
    IngestionError,
    apply_metadata,
    index_document,
    publish_document,
    resolve_source,
    slugify,
)
from app.providers.storage import build_key, get_object_store
from app.services.preservation import record_version

log = get_logger(__name__)

# The corpus writes dates as DD.MM.YYYY.
DATE_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$")
FRONTMATTER_LINE = re.compile(r"^\*\*[^*]+:\*\*")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
DASHES = re.compile(r"^[-=_*]{3,}$")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


@dataclass(slots=True)
class CleanedText:
    """The corpus text with its own generated front matter removed."""

    sections: list[tuple[str, str]] = field(default_factory=list)
    removed_tldr: bool = False

    @property
    def plain(self) -> str:
        return "\n\n".join(body for _title, body in self.sections).strip()

    @property
    def with_headings(self) -> str:
        """The same text with its headings kept.

        The chunker reads ``## Heading`` lines to label chunks, so a citation can
        say which section a passage came from. The headings are editorial titles
        supplied by the corpus, which is another reason these records cannot be
        quoted as primary text.
        """
        return "\n\n".join(
            f"## {title}\n\n{body}" for title, body in self.sections
        ).strip()


def parse_corpus_date(value: str | None) -> tuple[date | None, str | None]:
    if not value:
        return None, None
    m = DATE_RE.match(value.strip())
    if not m:
        return None, None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day), "day"
    except ValueError:
        return None, None


def clean_transcript(text: str) -> CleanedText:
    """Split the corpus text into sections and drop everything generated.

    The dataset wraps each record in material of its own making: a ``# Title``
    line, a ``**Field:** value`` block that repeats metadata we store in columns,
    and a ``## TL;DR`` summary. None of that is text from the cited source, so
    none of it is indexed. What remains is the body text, split on its headings
    so a citation can name the section it came from.
    """
    sections: list[tuple[str, str]] = []
    title: str | None = None
    buffer: list[str] = []
    removed_tldr = False
    body_started = False
    skip_until_level: int | None = None

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if body:
            sections.append((title or f"Section {len(sections) + 1}", body))

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not body_started:
            # --- header block: skip until a level-2 heading opens the body ---
            if not stripped:
                continue
            if FRONTMATTER_LINE.match(stripped):
                continue
            m = HEADING.match(stripped)
            if m and len(m.group(1)) >= 2:
                body_started = True
            elif m:
                continue  # the '#' document title
            else:
                # No heading at all: treat the whole thing as body text.
                body_started = True

        if skip_until_level is not None:
            m = HEADING.match(stripped)
            if m and len(m.group(1)) <= skip_until_level:
                skip_until_level = None
            else:
                continue

        if not stripped:
            if body_started:
                buffer.append("")
            continue

        m = HEADING.match(stripped)
        if m:
            level, heading = len(m.group(1)), m.group(2).strip()
            if re.sub(r"[^a-z0-9]", "", heading.lower()) == "tldr":
                removed_tldr = True
                buffer.clear()
                skip_until_level = level
                continue
            if level <= 1:
                continue
            flush()
            title = heading
            continue

        if DASHES.match(stripped):
            continue

        if LIST_ITEM.match(line):
            # keep the wording, drop the bullet glyph so the text reads as prose
            buffer.append(LIST_ITEM.sub("", line))
            continue

        buffer.append(line)

    flush()
    if not sections:
        fallback = "\n".join((text or "").splitlines()).strip()
        if fallback:
            sections.append(("Full text", fallback))
    return CleanedText(sections=sections, removed_tldr=removed_tldr)


def _ensure_event(
    db: Session, document: Document, record: dict[str, Any], year: int | None
) -> None:
    """Give the record a place on the timeline.

    The speech itself is the event, so the event and the document share a title
    and date rather than the event inventing its own framing.
    """
    from app.models.knowledge import Event, EventDocument

    event = Event(
        slug=document.slug,
        title=document.title,
        description=document.summary,
        event_date=document.document_date,
        date_label=(
            document.document_date.strftime("%d %B %Y")
            if document.document_date
            else (str(year) if year else None)
        ),
        year=year,
        date_precision=document.date_precision or "year",
        event_type=(record.get("event_type") or None),
        place_label=(record.get("location") or None),
        source_id=document.source_id,
        source_url=document.source_url,
        significance=None,
        is_demo=False,
    )
    db.add(event)
    db.flush()
    db.add(EventDocument(event_id=event.id, document_id=document.id, relation="documented_by"))


def unique_slug(base: str, taken: set[str]) -> str:
    slug = slugify(base) or "record"
    candidate = slug
    suffix = 2
    while candidate in taken:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def import_one(
    db: Session,
    record: dict[str, Any],
    *,
    raw_line: str,
    taken_slugs: set[str],
    collection_id: str | None,
    publish: bool,
    run_graph: bool,
) -> Document:
    speech_id = (record.get("speech_id") or "").strip()
    # Three speech_ids in the dataset are reused for genuinely different events
    # (e.g. SPEECH-BRA-1948-POWERS covers both 25.11.1948 and 10.12.1948), so the
    # date is part of the identity. Collapsing them would silently drop records.
    record_date = (record.get("date") or "").strip()
    external_key = f"{speech_id}@{record_date}" if record_date else speech_id
    title = (record.get("title") or speech_id or "Untitled record").strip()
    document_date, precision = parse_corpus_date(record.get("date"))
    year = record.get("year")
    try:
        year_int = int(year) if year else (document_date.year if document_date else None)
    except (TypeError, ValueError):
        year_int = document_date.year if document_date else None

    cleaned = clean_transcript(record.get("transcript") or "")
    source_url = (record.get("source_url") or "").strip() or None
    source_domain = (record.get("source_domain") or "").strip() or None
    discovery = (record.get("discovery_source") or "").strip() or None
    source_type = (record.get("source_type") or "").strip() or None
    rights = (record.get("rights_status") or "").strip() or None

    source = resolve_source(
        db,
        source_url=source_url,
        source_name=discovery or source_domain or "Ambedkar speech corpus",
        source_type=source_type,
        rights=rights,
        publisher=source_domain,
    )

    # The digest the corpus claims for this record. It is stored as provenance,
    # never used to assert that our text matches the cited original.
    corpus_digest = (record.get("sha256") or "").strip() or None

    provenance = (
        f"Imported from the ambedkar-speech-corpus dataset (Apache-2.0), record "
        f"{external_key or 'unknown'}. Cited source: {source_url or 'not supplied'}"
        + (f" (retrieved via {discovery})" if discovery else "")
        + ". The text stored here is a third-party summary whose fidelity to the "
        "cited source has not been checked."
    )
    editorial_note = (
        "Unverified secondary text. The corpus labels this record "
        f"'transcript_status: {record.get('transcript_status')}' and "
        f"'transcript_confidence: {record.get('transcript_confidence')}', but inspection "
        "shows a ~1,000-character modern retelling with a generated summary section, "
        "not a archival transcription. It is searchable and citable as a summary; it "
        "must not be quoted as Dr. Ambedkar's words. Verify against the cited source "
        "before promoting it to a primary text."
        + (" The generated '## TL;DR' block was removed on import." if cleaned.removed_tldr else "")
    )

    document = Document(
        slug=unique_slug(f"{title[:120]}-{year_int or 'nd'}", taken_slugs),
        title=title[:500],
        summary=(record.get("tldr") or None),
        speaker=(record.get("speaker") or "Dr. B. R. Ambedkar")[:300],
        author_display="Dr. B. R. Ambedkar",
        document_type=DocumentType.SPEECH.value,
        language="en",
        original_language="en",
        document_date=document_date,
        year=year_int,
        date_precision=precision,
        location_text=(record.get("location") or None),
        venue=(record.get("venue") or None),
        event_type=(record.get("event_type") or None),
        collection_id=collection_id,
        source_id=source.id if source else None,
        source_url=source_url,
        source_reference=external_key or None,
        external_id=external_key or None,
        rights=rights,
        provenance=provenance,
        verification_status=VerificationStatus.UNVERIFIED_SECONDARY,
        editorial_note=editorial_note,
        checksum_sha256=corpus_digest,
        mime_type="text/markdown",
        processing_state=ProcessingState.READY,
    )
    db.add(document)
    db.flush()

    # Preserve the bytes we actually ingested: the raw corpus line and the
    # cleaned text used for indexing. Both are immutable and verifiable.
    store = get_object_store()
    raw_key = build_key(
        "documents", document.slug, f"raw-corpus-record-{external_key or 'unknown'}.jsonl"
    )
    text_key = build_key("documents", document.slug, "corpus-text.md")
    store.put(raw_key, raw_line.encode("utf-8"), "application/x-ndjson")
    store.put(text_key, cleaned.plain.encode("utf-8"), "text/markdown")
    document.storage_key = text_key
    document.byte_size = len(cleaned.plain.encode("utf-8"))

    record_version(
        db,
        document,
        data=raw_line.encode("utf-8"),
        storage_key=raw_key,
        mime_type="application/x-ndjson",
        note="Raw corpus record as supplied by the dataset, preserved unmodified.",
    )
    record_version(
        db,
        document,
        data=cleaned.plain.encode("utf-8"),
        storage_key=text_key,
        mime_type="text/markdown",
        note="Indexed text: corpus content with the generated title, front matter "
        "and TL;DR block removed. Unverified secondary text.",
    )

    # page 0 means "no pagination": these records are single continuous texts
    # with no page images, so a citation must not claim a page number.
    index_document(db, document, pages=[(0, cleaned.with_headings)])

    if document_date is not None or year_int is not None:
        _ensure_event(db, document, record, year_int)

    if run_graph:
        from app.services.knowledge import extract_for_document

        extract_for_document(db, document, auto_publish=False)

    if publish:
        publish_document(db, document)
    else:
        db.commit()
    return document


def ensure_collection(db: Session, title: str, description: str) -> str:
    collection = db.scalar(select(Collection).where(Collection.slug == slugify(title)))
    if collection is None:
        collection = Collection(
            slug=slugify(title),
            title=title,
            description=description,
            is_published=True,
        )
        db.add(collection)
        db.flush()
    return collection.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="Path to speeches.jsonl")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish imported records (default: leave as drafts for review)",
    )
    parser.add_argument(
        "--no-graph", action="store_true", help="Skip entity/relationship extraction"
    )
    parser.add_argument("--limit", type=int, default=0, help="Import only the first N records")
    parser.add_argument(
        "--only", default=None, help="Import a single record by speech_id or external_id"
    )
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help=(
            "Exit successfully without importing if the archive already holds "
            "documents. For a deployment that imports on every cold start, so a "
            "restart costs a query rather than a full reindex."
        ),
    )
    args = parser.parse_args(argv)

    configure_logging()

    if args.if_empty:
        from sqlalchemy import func, select as _select

        from app.db.base import session_scope as _session_scope
        from app.models.archive import Document as _Document

        with _session_scope() as db:
            already = db.scalar(
                _select(func.count()).select_from(_Document).where(
                    _Document.deleted_at.is_(None)
                )
            )
        if already:
            log.info("archive already populated; nothing to do", documents=already)
            return 0

    path = Path(args.path).expanduser()
    if not path.exists():
        log.error("corpus file not found", path=str(path))
        return 2

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        lines = lines[: args.limit]

    records: list[tuple[dict[str, Any], str]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("skipping malformed line", error=str(exc))
            continue
        if args.only and args.only not in {
            record.get("speech_id"),
            record.get("canonical_event_id"),
            f"{record.get('speech_id')}@{record.get('date')}",
        }:
            continue
        records.append((record, line))

    if not records:
        log.error("no records matched", path=str(path), only=args.only)
        return 2

    imported = 0
    skipped = 0
    with session_scope() as db:
        collection_id = ensure_collection(
            db,
            "Ambedkar speeches (catalogue)",
            "Catalogue of speeches and addresses attributed to Dr. B. R. Ambedkar, "
            "imported from a public research corpus. The stored text is an "
            "unverified secondary summary of each cited source.",
        )
        taken_slugs = set(db.scalars(select(Document.slug)).all())
        for record, raw_line in records:
            external = record.get("speech_id")
            record_date = (record.get("date") or "").strip()
            external_key = f"{external}@{record_date}" if record_date else external
            existing = db.scalar(
                select(Document).where(Document.external_id == external_key)
            ) if external_key else None
            if existing is not None:
                skipped += 1
                continue
            try:
                document = import_one(
                    db,
                    record,
                    raw_line=raw_line,
                    taken_slugs=taken_slugs,
                    collection_id=collection_id,
                    publish=args.publish,
                    run_graph=not args.no_graph,
                )
            except IngestionError as exc:
                db.rollback()
                skipped += 1
                log.warning("record failed", external_id=external, error=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                skipped += 1
                log.error("record errored", external_id=external, error=str(exc))
                continue
            imported += 1
            if imported % 25 == 0:
                log.info("progress", imported=imported, remaining=len(records) - imported - skipped)

    log.info(
        "import finished",
        imported=imported,
        skipped=skipped,
        published=args.publish,
        graph=not args.no_graph,
    )
    print(
        f"Imported {imported} record(s); {skipped} skipped (already present or failed).\n"
        "Every record is marked verification_status=unverified_secondary with "
        "quote_verified=false chunks, so the RAG layer will not quote it as "
        "Dr. Ambedkar's words."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
