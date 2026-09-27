"""Check a document's stored text against its cited original, and promote it.

The archive refuses to present unchecked text as Dr. Ambedkar's words, so a
document stays ``unverified_secondary`` until someone has compared it with the
archival source. This tool performs that comparison and records the outcome.

Design rules, in order of importance:

1. **Never promote on a weak match.** A corpus that paraphrases its source will
   score far below the threshold even when it is being scrupulously honest. The
   default behaviour is to report and refuse; promotion needs an explicit
   ``--promote`` *and* a human reviewer *and* a match above the threshold.
2. **Report what the comparison can and cannot show.** Automatic text comparison
   can demonstrate that text is *not* verbatim. It cannot certify that a text is
   a faithful transcript, because it cannot tell an accurate transcription from a
   plausible paraphrase. So the report says so, and the reviewer is the one who
   decides.
3. **Leave a trail.** Every run writes an audit entry, promoted or not.

Usage::

    PYTHONPATH=. python scripts/verify_source.py --list
    PYTHONPATH=. python scripts/verify_source.py --slug castes-in-india... --report
    PYTHONPATH=. python scripts/verify_source.py --slug <slug> \\
        --promote --reviewer archivist@sih.local --character-threshold 0.97
"""

from __future__ import annotations

import argparse
import difflib
import re
import ssl
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select


# Allow "python scripts/<name>.py" from anywhere, which is how the README
# documents these commands, without requiring PYTHONPATH to be set first.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from app.core.logging import configure_logging, get_logger
from app.db.base import session_scope
from app.models.archive import Document, DocumentChunk
from app.models.enums import VerificationStatus
from app.services.audit import record as record_audit

log = get_logger(__name__)

# Retrieval is polite: a named user agent and a timeout, no crawling of links.
USER_AGENT = "SIH26096-dha-verify/1.0 (provenance check; one request per document)"
TIMEOUT_SECONDS = 30
MAX_SOURCE_BYTES = 8 * 1024 * 1024

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class Comparison:
    """What comparing two texts can actually establish."""

    verdict: str
    similarity: float
    stored_characters: int
    source_characters: int
    stored_sha256: str
    source_sha256: str
    source_url: str
    fetched: bool
    sample_matched: int
    sample_total: int
    samples: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "similarity": round(self.similarity, 4),
            "stored_characters": self.stored_characters,
            "source_characters": self.source_characters,
            "stored_sha256": self.stored_sha256,
            "source_sha256": self.source_sha256,
            "source_url": self.source_url,
            "fetched": self.fetched,
            "sample_matched": self.sample_matched,
            "sample_total": self.sample_total,
            "samples": self.samples,
            "notes": self.notes,
        }


def normalise(text: str) -> str:
    """Strip markup and collapse whitespace, so formatting is not compared."""
    without_tags = _TAG_RE.sub(" ", text)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&mdash;", "-"),
        ("&ndash;", "-"),
    ):
        without_tags = without_tags.replace(entity, char)
    return _WS_RE.sub(" ", without_tags).strip()


def strip_html(raw: str) -> str:
    """Very small HTML-to-text pass: enough to compare a scholarly edition."""
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    return normalise(text)


def _ssl_context() -> ssl.SSLContext:
    """TLS verification, using certifi's bundle when the interpreter has none.

    Some Python builds (notably the framework build used on macOS) ship without a
    CA bundle, which makes every HTTPS fetch fail. certifi is already a transitive
    dependency, so prefer it. Verification is never disabled: a provenance tool
    that cannot authenticate the source it is checking is worse than useless.
    """
    context = ssl.create_default_context()
    try:
        import certifi

        context.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - fall back to the system store
        if not (ssl.get_default_verify_paths().cafile or ssl.get_default_verify_paths().capath):
            raise
    return context


def fetch_source(url: str) -> tuple[str | None, str]:
    """Return (text, note). text is None when the source could not be read."""
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=TIMEOUT_SECONDS, context=_ssl_context()) as response:  # noqa: S310
            raw = response.read(MAX_SOURCE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        if exc.code == 403:
            return None, (
                f"The server refused the request (HTTP 403). Many archive sites sit "
                f"behind a bot challenge, which this tool will not try to defeat. Open "
                f"the URL in a browser, compare the text by hand, and record the outcome "
                f"with --reviewer."
            )
        return None, f"Could not read the source: HTTP {exc.code} {exc.reason}"
    except (URLError, OSError, ValueError) as exc:
        return None, f"Could not read the source: {exc}"
    if len(raw) > MAX_SOURCE_BYTES:
        return None, "The source is larger than this tool will download."
    text = raw.decode(charset, errors="replace")
    if "<" in text and ">" in text:
        return strip_html(text), "HTML was converted to plain text before comparing."
    return normalise(text), ""


def sample_passages(stored: str, limit: int = 12) -> list[tuple[str, str]]:
    """Split the stored text into passages and pair each with the source span.

    A whole-document ratio punishes a source that is much longer than the
    document it contains, so the useful signal is whether the document's own
    sentences appear verbatim in the source.
    """
    passages: list[tuple[str, str]] = []
    for block in re.split(r"\n{2,}|(?<=[.!?])\s{2,}", stored):
        block = block.strip()
        if len(block) >= 60:
            passages.append((block[:20000], block[:120]))
        if len(passages) >= limit:
            break
    return passages


def compare(
    stored: str, source: str, source_url: str, *, sample_limit: int = 12
) -> Comparison:
    from app.core.security import sha256_bytes

    stored_norm = normalise(stored)
    source_norm = source
    ratio = difflib.SequenceMatcher(None, stored_norm[:200_000], source_norm[:200_000])
    similarity = ratio.quick_ratio()

    samples: list[dict[str, object]] = []
    matched = 0
    for passage, label in sample_passages(stored_norm, sample_limit):
        found = passage in source_norm
        matched += int(found)
        samples.append({"passage": label, "verbatim_in_source": found})

    total = len(samples)
    notes: list[str] = []
    if not source_norm:
        return Comparison(
            verdict="no_source_text",
            similarity=0.0,
            stored_characters=len(stored_norm),
            source_characters=0,
            stored_sha256=sha256_bytes(stored_norm.encode()),
            source_sha256="",
            source_url=source_url,
            fetched=False,
            sample_matched=0,
            sample_total=0,
            notes=["The source could not be read, so no comparison was made."],
        )

    if total and matched == total:
        verdict = "verbatim"
    elif total and matched / total >= 0.9:
        verdict = "near_verbatim"
    elif total and matched / total >= 0.5:
        verdict = "partially_verbatim"
    else:
        verdict = "not_verbatim"
        notes.append(
            "Most passages do not appear in the source. This is consistent with a "
            "modern paraphrase or summary rather than a transcription."
        )
    if similarity < 0.5:
        notes.append(
            f"Whole-document similarity is low ({similarity:.2f}); the source may contain "
            "much more material than this document."
        )
    notes.append(
        "An automatic comparison can show that text is not verbatim. It cannot certify "
        "that a text is a faithful transcript, so a human reviewer must still decide."
    )
    return Comparison(
        verdict=verdict,
        similarity=similarity,
        stored_characters=len(stored_norm),
        source_characters=len(source_norm),
        stored_sha256=sha256_bytes(stored_norm.encode()),
        source_sha256=sha256_bytes(source_norm.encode()),
        source_url=source_url,
        fetched=True,
        sample_matched=matched,
        sample_total=total,
        samples=samples,
        notes=notes,
    )


def stored_text(db, document: Document) -> str:
    parts = [
        chunk.text
        for chunk in db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
    ]
    return "\n\n".join(parts)


def promote(db, document: Document, comparison: Comparison, reviewer_email: str) -> None:
    document.verification_status = VerificationStatus.VERIFIED_PRIMARY
    # Quote-verification is a property of the text, so it is promoted with the
    # document: otherwise a promoted document would still refuse to be quoted.
    for chunk in db.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id)
    ).all():
        chunk.quote_verified = True
    existing = document.provenance or ""
    document.provenance = (
        f"{existing}\nVerified against the cited source on the basis of a character-level "
        f"comparison ({comparison.verdict}, {comparison.sample_matched}/"
        f"{comparison.sample_total} sampled passages verbatim) reviewed by "
        f"{reviewer_email}. Source digest {comparison.source_sha256}."
    ).strip()
    record_audit(
        db,
        action="source.verify",
        actor=None,
        entity_type="document",
        entity_id=document.id,
        detail={
            "slug": document.slug,
            "reviewer": reviewer_email,
            "verdict": comparison.verdict,
            "similarity": round(comparison.similarity, 4),
            "samples_matched": comparison.sample_matched,
            "samples_total": comparison.sample_total,
            "source_url": comparison.source_url,
            "source_sha256": comparison.source_sha256,
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a document with its cited source and, with a reviewer's "
        "approval, promote it to verified_primary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--slug", help="Slug or document id to check.")
    parser.add_argument("--limit", type=int, default=20, help="With --list, how many rows.")
    parser.add_argument("--list", action="store_true", help="List unverified documents.")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Mark the document verified. Requires --reviewer and a passing comparison.",
    )
    parser.add_argument("--reviewer", help="Email of the human taking responsibility.")
    parser.add_argument(
        "--character-threshold",
        type=float,
        default=0.97,
        help="Minimum whole-document similarity required to promote (default 0.97).",
    )
    parser.add_argument(
        "--sample-threshold",
        type=float,
        default=1.0,
        help="Minimum share of sampled passages that must be verbatim (default 1.0).",
    )
    args = parser.parse_args(argv)

    if args.promote and not args.reviewer:
        print("--promote requires --reviewer: a person must stand behind the decision.")
        return 2
    if not args.list and not args.slug:
        print("Pass --slug, or --list to see what still needs checking.")
        return 2

    configure_logging()
    with session_scope() as db:
        if args.list:
            rows = db.scalars(
                select(Document)
                .where(
                    Document.verification_status != VerificationStatus.VERIFIED_PRIMARY,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.document_date.desc().nullslast())
                .limit(args.limit)
            ).all()
            print(f"{len(rows)} document(s) awaiting source verification:\n")
            for row in rows:
                print(
                    f"  {row.verification_status:<20} {str(row.year or '----'):<5} "
                    f"{row.slug[:60]:<62} {row.title[:40]}"
                )
            if not rows:
                print("  Every document has been checked against a source.")
            return 0

        document = db.scalar(
            select(Document).where(
                (Document.slug == args.slug) | (Document.id == args.slug)
            )
        )
        if document is None:
            print(f"No document matches {args.slug!r}.", file=sys.stderr)
            return 1

        url = document.source_url or ""
        if not url:
            print(
                f"{document.slug} has no cited source URL, so there is nothing to compare "
                "against. Add the archival source to the record first.",
                file=sys.stderr,
            )
            return 1

        print(f"Document : {document.title}")
        print(f"Slug     : {document.slug}")
        print(f"Status   : {document.verification_status}")
        print(f"Source   : {url}\n")

        text = stored_text(db, document)
        fetched, note = fetch_source(url)
        if note:
            print(f"Note     : {note}")
        if fetched is None:
            print(
                "\nThe source could not be retrieved, so the document stays unverified. "
                "Check the URL, or verify by hand and record the outcome."
            )
            return 1

        comparison = compare(text, fetched, url)
        print(f"Verdict  : {comparison.verdict}")
        print(f"Similarity (whole document, characters): {comparison.similarity:.4f}")
        print(
            f"Sampled passages verbatim in source: "
            f"{comparison.sample_matched}/{comparison.sample_total}"
        )
        for note in comparison.notes:
            print(f"          - {note}")

        if not args.promote:
            print("\n--promote was not given, so nothing was changed.")
            return 0

        sample_share = (
            comparison.sample_matched / comparison.sample_total
            if comparison.sample_total
            else 0.0
        )
        failures: list[str] = []
        if comparison.verdict != "verbatim":
            failures.append(f"verdict is {comparison.verdict}, not verbatim")
        if sample_share < args.sample_threshold:
            failures.append(
                f"only {sample_share:.0%} of sampled passages are verbatim "
                f"(required {args.sample_threshold:.0%})"
            )
        if comparison.similarity < args.character_threshold:
            failures.append(
                f"similarity {comparison.similarity:.4f} is below "
                f"{args.character_threshold:.2f}"
            )
        if failures:
            print("\nNot promoting. " + "; ".join(failures) + ".")
            print(
                "If the text is a legitimate transcription recorded elsewhere, verify it "
                "against that source instead."
            )
            record_audit(
                db,
                action="source.verify_refused",
                actor=None,
                entity_type="document",
                entity_id=document.id,
                detail={
                    "reviewer": args.reviewer,
                    "verdict": comparison.verdict,
                    "similarity": round(comparison.similarity, 4),
                    "reasons": failures,
                },
            )
            return 1

        promote(db, document, comparison, args.reviewer)
        print(
            f"\nPromoted to verified_primary, reviewed by {args.reviewer}. "
            "Its chunks may now be quoted."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
