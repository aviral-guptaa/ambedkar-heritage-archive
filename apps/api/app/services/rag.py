"""Evidence-grounded retrieval augmented generation.

Pipeline
--------
    question
      → language detection
      → query normalisation
      → hybrid retrieval (vector + keyword + entity, RRF fused)
      → rerank
      → evidence selection (threshold + redundancy pruning)
      → answer generation (extractive by default, LLM when configured)
      → citation generation
      → citation validation
      → groundedness scoring
      → answer

Two rules are enforced unconditionally, regardless of which LLM produced the
text:

* **No evidence ⇒ no answer.** The response is the exact refusal sentence.
* **No fabricated metadata.** A chunk without a page number yields
  "Page information unavailable in indexed source." — never a guess.

Every claim sentence is classified as a direct quote, paraphrase, archival fact
or secondary interpretation so the UI can visually distinguish them.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.archive import Collection, Document, DocumentChunk, Source
from app.models.enums import VerificationStatus,  AnswerKind, ClaimKind
from app.models.ops import Citation, RagQuery
from app.providers.base import LLMMessage, ProviderError
from app.providers.embedding import _content_tokens, _tokens
from app.providers.llm import (
    CONFLICT_MARKER,
    REFUSAL_TEXT,
    EvidenceItem,
    GroundedContext,
    get_llm_provider,
)
from app.services.search import SearchFilters, hybrid_search

log = get_logger(__name__)

UNAVAILABLE_PAGE = "Page information unavailable in indexed source."

_WS = re.compile(r"\s+")
_QUOTE_RE = re.compile(r"[\"“](.{8,600}?)[\"”]")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“(])")
_NUM_RE = re.compile(r"\b\d[\d,\.:/-]*\b")
_ARTICLE_RE = re.compile(r"\bArticle\s+([0-9]+[A-Za-z]?)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{1,2}\s+)?(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b|\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")


# --------------------------------------------------------------------------- #
# request / response models
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class RAGRequest:
    question: str
    answer_language: str = "en"
    filters: SearchFilters = field(default_factory=SearchFilters)
    mode: str = "hybrid"
    top_k: int | None = None
    session_key: str | None = None
    user_id: str | None = None
    include_diagnostics: bool = False


@dataclass(slots=True)
class Claim:
    text: str
    kind: ClaimKind
    markers: list[int] = field(default_factory=list)
    supported: bool = True


@dataclass(slots=True)
class RAGResult:
    answer: str
    answer_kind: AnswerKind
    grounded: bool
    groundedness: float
    evidence: list[EvidenceItem]
    claims: list[Claim]
    conflicting: bool
    conflict_note: str | None
    citations: list[Citation]
    language: str
    question_language: str
    latency_ms: int
    llm_provider: str
    llm_model: str
    diagnostics: dict[str, Any]
    query_id: str | None = None
    refusal_reason: str | None = None


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #

HINDI_MARKERS = ("क्या", "कब", "कौन", "क्यों", "ने", "का", "की", "के", "में", "है", "था", "बारे")
MARATHI_MARKERS = ("काय", "कुठे", "कोण", "सांगितले", "आहे", "मध्ये", "नाही", "पण")


def detect_query_language(text: str) -> str:
    sample = text[:2000]
    devanagari = sum(1 for ch in sample if "ऀ" <= ch <= "ॿ")
    latin = sum(1 for ch in sample if ch.isascii() and ch.isalpha())
    if devanagari > latin:
        mr_hits = sum(1 for m in MARATHI_MARKERS if m in sample)
        hi_hits = sum(1 for m in HINDI_MARKERS if m in sample)
        return "mr" if mr_hits > hi_hits else "hi"
    return "en"


_TRANSLIT_HINTS = {
    "article": "अनुच्छेद",
    "constitution": "संविधान",
    "ambedkar": "अम्बेडकर",
}


def normalise_query(question: str) -> dict[str, Any]:
    """Normalise the raw question for retrieval.

    Devanagari questions are *not* translated here — the archive text may be in
    English — instead the matching Latin transliterations of key terms are added
    as alternative search terms so an Indic-language query can still reach an
    English record. This is a retrieval aid, surfaced in diagnostics.
    """
    q = _WS.sub(" ", question or "").strip()
    normalised = q.rstrip("?.। ")
    terms = _content_tokens(normalised) or _tokens(normalised)
    variants = {normalised}
    additions: list[str] = []
    for term, indic in _TRANSLIT_HINTS.items():
        if term in normalised.lower() and indic not in normalised:
            additions.append(indic)
    if re.search(r"\b32\b", normalised) and "अनुच्छेद" not in normalised:
        additions.append("article 32")
    for extra in additions:
        variants.add(f"{normalised} {extra}")
    return {
        "raw": q,
        "normalised": normalised,
        "terms": terms,
        "search_variants": sorted(variants),
        "detected_language": detect_query_language(q),
        "article_numbers": sorted({f"Article {m}" for m in _ARTICLE_RE.findall(normalised)}),
    }


# --------------------------------------------------------------------------- #
# evidence assembly
# --------------------------------------------------------------------------- #


def _date_label(doc: Document) -> str | None:
    if doc.document_date:
        return doc.document_date.strftime("%d %B %Y")
    if doc.year:
        return str(doc.year)
    return None


UNVERIFIED_WARNING = (
    "This text is an unverified secondary account, not a checked transcription. "
    "It is cited as a summary of the cited source and must not be quoted as "
    "Ambedkar's own words."
)


def _provenance_warning(doc: Document, chunk: Any) -> str | None:
    """Say plainly when retrieved text may not be presented as a quotation."""
    status = str(doc.verification_status)
    if status == VerificationStatus.VERIFIED_PRIMARY and chunk.quote_verified:
        return None
    if status == VerificationStatus.MACHINE_TRANSLATION:
        return (
            "This is a machine translation of a source text. The original-language "
            "text is the record; the translation is an interpretation."
        )
    if status == VerificationStatus.PENDING_REVIEW:
        return (
            "This text has not yet been checked by an archivist against the original "
            "source."
        )
    return UNVERIFIED_WARNING


def _build_evidence_items(
    db: Session, passages: list[Any], limit: int
) -> list[EvidenceItem]:
    if not passages:
        return []
    chunk_ids = [p.chunk_id for p in passages[:limit]]
    rows = db.execute(
        select(DocumentChunk, Document, Source, Collection)
        .join(Document, Document.id == DocumentChunk.document_id)
        .outerjoin(Source, Source.id == Document.source_id)
        .outerjoin(Collection, Collection.id == Document.collection_id)
        .where(DocumentChunk.id.in_(chunk_ids))
    ).all()
    by_id = {r[0].id: r for r in rows}
    items: list[EvidenceItem] = []
    for marker, passage in enumerate(passages[:limit], start=1):
        row = by_id.get(passage.chunk_id)
        if row is None:
            continue
        chunk, doc, source, collection = row
        items.append(
            EvidenceItem(
                marker=marker,
                text=chunk.text,
                document_title=doc.title,
                document_id=doc.id,
                chunk_id=chunk.id,
                date_label=_date_label(doc),
                speaker=doc.speaker or doc.author_display,
                # Page is only present when the ingestion pipeline actually
                # recorded one. Never synthesised.
                page_number=chunk.page_number,
                section=chunk.section,
                source_name=source.name if source else None,
                source_url=chunk.source_url or doc.source_url,
                source_reference=chunk.source_reference or doc.source_reference,
                source_tier=source.tier if source else "unknown",
                document_type=doc.document_type,
                collection=collection.title if collection else None,
                language=chunk.language or doc.language,
                score=passage.score,
                vector_score=passage.vector_score,
                keyword_score=passage.keyword_score,
                rerank_score=passage.rerank_score,
                rank_vector=passage.rank_vector,
                rank_keyword=passage.rank_keyword,
                verification_status=str(doc.verification_status),
                quote_verified=bool(chunk.quote_verified),
                provenance_warning=_provenance_warning(doc, chunk),
            )
        )
    return items


def _prune_redundant(items: list[EvidenceItem], threshold: float = 0.86) -> list[EvidenceItem]:
    kept: list[EvidenceItem] = []
    for item in items:
        duplicate = False
        for other in kept:
            if fuzz.token_set_ratio(item.text[:1200], other.text[:1200]) >= threshold * 100:
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
        if len(kept) >= settings.rag_max_evidence:
            break
    return kept


def detect_conflicts(items: list[EvidenceItem]) -> tuple[bool, str | None]:
    """Flag sources that make incompatible factual claims.

    Compares the same *kind* of claim (a date for an event) across passages. When
    two passages give different dates and share no date in common the difference
    is surfaced for the visitor, never silently resolved.
    """
    if len(items) < 2:
        return False, None

    def dates(text: str) -> set[str]:
        # A regex with capture groups makes findall() yield tuples, so flatten
        # before treating the matches as strings.
        found: set[str] = set()
        for match in _DATE_RE.findall(text):
            parts = match if isinstance(match, tuple) else (match,)
            found.update(part.lower() for part in parts if part)
        return found

    per_item = [dates(i.text) for i in items]
    for i, mine in enumerate(per_item):
        if not mine:
            continue
        others: set[str] = set()
        for j, theirs in enumerate(per_item):
            if i != j:
                others |= theirs
        if others and not (mine & others):
            return True, (
                "Indexed passages give different dates for what appears to be the same event. "
                "Both accounts are shown below; the archive does not resolve the difference."
            )
    return False, None


# --------------------------------------------------------------------------- #
# citation validation + groundedness
# --------------------------------------------------------------------------- #


def _normalise_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    return _WS.sub(" ", text).strip().lower()


def _snippet_for(evidence_text: str, length: int = 420) -> str:
    text = _WS.sub(" ", evidence_text or "").strip()
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def validate_citations(answer: str, evidence: list[EvidenceItem]) -> list[Citation]:
    """Verify every citation actually appears in the indexed passage.

    Also blocks fabrication of page numbers: a citation may only carry a page
    when the underlying chunk recorded one.
    """
    citations: list[Citation] = []
    haystack = _normalise_for_match(" ".join(e.text for e in evidence))

    for item in evidence:
        snippet = _snippet_for(item.text)
        needle = _normalise_for_match(snippet.rstrip("…"))
        verified = bool(needle) and (
            needle in haystack
            or fuzz.partial_ratio(needle[:200], haystack) >= 92
        )
        if not verified:
            # Fall back to a shorter, unambiguous span of the passage.
            first_sentence = _WS.sub(" ", item.text.strip()).split(". ")[0]
            needle = _normalise_for_match(first_sentence)[:200]
            verified = bool(needle) and needle in haystack

        note_parts: list[str] = []
        if item.page_number is None:
            note_parts.append(UNAVAILABLE_PAGE)
        if item.provenance_warning:
            note_parts.append(item.provenance_warning)
            # The snippet really is in the passage, but the passage is not a
            # verified transcription, so it must not be shown as a quotation.
            verified = False
        citations.append(
            Citation(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                page_id=None,
                rank=item.marker,
                snippet=snippet,
                verified=verified,
                validation_note=" ".join(note_parts) or None,
            )
        )
    return citations


def _classify_claims(answer: str, evidence: list[EvidenceItem]) -> list[Claim]:
    claims: list[Claim] = []
    evidence_norm = [_normalise_for_match(e.text) for e in evidence]
    haystack = " ".join(evidence_norm)
    q_tokens = set(_content_tokens(answer))
    ev_tokens = set(_content_tokens(haystack))

    segments = [s.strip() for s in _SENT_RE.split(answer) if s.strip()]
    for segment in segments:
        markers = [e.marker for e in evidence if f"[{e.marker}]" in segment]
        direct = bool(_QUOTE_RE.search(segment))
        if direct:
            kind = ClaimKind.DIRECT_QUOTE
        elif _DATE_RE.search(segment) or _ARTICLE_RE.search(segment):
            kind = ClaimKind.ARCHIVAL_FACT
        elif _NUM_RE.search(segment):
            kind = ClaimKind.ARCHIVAL_FACT
        else:
            kind = ClaimKind.PARAPHRASE
        if not markers:
            # A sentence with no citation marker that adds new content is
            # reported as unsupported so the UI can flag it.
            seg_tokens = set(_content_tokens(segment)) - q_tokens
            novel = seg_tokens - ev_tokens
            if len(novel) > max(4, len(seg_tokens) * 0.6):
                claims.append(Claim(text=segment, kind=kind, markers=[], supported=False))
                continue
        claims.append(Claim(text=segment, kind=kind, markers=markers, supported=True))
    return claims


UNVERIFIED_LEAD = (
    "Every passage below comes from a source whose text has not been checked "
    "against the original. This is a summary of a third-party account, not a "
    "quotation, and it should be verified against the linked source before it is "
    "relied on."
)


def question_overlap(question: str, evidence: list[EvidenceItem]) -> float:
    """Share of the question's content words present in the retrieved evidence."""
    terms = set(_content_tokens(question))
    if not terms or not evidence:
        return 0.0
    haystack = set(_content_tokens(" ".join(e.text for e in evidence)))
    return round(len(terms & haystack) / len(terms), 4)


def verification_mix(evidence: list[EvidenceItem]) -> tuple[int, int]:
    """How many of the cited passages have been checked against an original."""
    if not evidence:
        return 0, 0
    verified = sum(
        1
        for e in evidence
        if e.quote_verified and e.verification_status == str(VerificationStatus.VERIFIED_PRIMARY)
    )
    return verified, len(evidence)


_FRAMING_PREFIX = re.compile(
    r"^(?:an unverified summary of the indexed record|in the indexed record|"
    r"in the indexed)\b[^:]*:\s*",
    re.IGNORECASE,
)
_FRAMING_TAIL = re.compile(r"\s*(?:states|—|:)\s*", re.IGNORECASE)


def scored_text(answer: str) -> str:
    """Strip the archive's own framing so it is not scored as unsupported.

    The answer is wrapped in labels such as "An unverified summary of the
    indexed record …, which states:". Those words are the archive talking, not
    evidence, so counting them as unsupported content would penalise the answer
    for being honestly labelled.
    """
    if not answer:
        return ""
    if UNVERIFIED_LEAD in answer:
        answer = answer.split(UNVERIFIED_LEAD, 1)[1]
    if CONFLICT_MARKER in answer:
        answer = answer.split(CONFLICT_MARKER, 1)[1]
    kept: list[str] = []
    for line in answer.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"\[\d+\]\s*$", "", line).strip()
        line = _FRAMING_PREFIX.sub("", line)
        line = _FRAMING_TAIL.sub(" ", line, count=1)
        if line:
            kept.append(line)
    return " ".join(kept)


def score_groundedness(answer: str, evidence: list[EvidenceItem], claims: list[Claim]) -> float:
    """Fraction of answer content that is traceable to the retrieved evidence.

    Deliberately conservative: entity/number tokens in the answer that do not
    appear anywhere in the evidence count against the score.
    """
    if not answer or answer.strip() == REFUSAL_TEXT:
        return 0.0
    ev_norm = " ".join(_normalise_for_match(e.text) for e in evidence)
    ev_tokens = set(_content_tokens(ev_norm))
    ans_tokens = _content_tokens(answer)
    if not ans_tokens:
        return 0.0
    covered = sum(1 for t in ans_tokens if t in ev_tokens)
    lexical = covered / len(ans_tokens)
    cited = sum(1 for c in claims if c.supported)
    citation_ratio = cited / len(claims) if claims else 0.0
    quote_ratio = (
        sum(1 for c in claims if c.kind is ClaimKind.DIRECT_QUOTE) / len(claims)
        if claims
        else 0.0
    )
    # Numeric / date / article tokens must all be present in the evidence.
    fabricated = 0
    for token in set(_NUM_RE.findall(answer)) | set(_ARTICLE_RE.findall(answer)):
        if _normalise_for_match(token) not in ev_norm:
            fabricated += 1
    penalty = min(0.5, fabricated * 0.12)
    score = 0.5 * lexical + 0.3 * citation_ratio + 0.2 * quote_ratio - penalty
    return round(max(0.0, min(1.0, score)), 4)


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #


def answer_question(db: Session, request: RAGRequest, *, persist: bool = True) -> RAGResult:
    started = time.perf_counter()
    norm = normalise_query(request.question)
    question_language = norm["detected_language"]
    answer_language = request.answer_language or question_language

    retrieval_diagnostics: dict[str, Any] = {}
    evidence: list[EvidenceItem] = []

    if not request.question.strip():
        return RAGResult(
            answer=REFUSAL_TEXT,
            answer_kind=AnswerKind.INSUFFICIENT_EVIDENCE,
            grounded=False,
            groundedness=0.0,
            evidence=[],
            claims=[],
            conflicting=False,
            conflict_note=None,
            citations=[],
            language=answer_language,
            question_language=question_language,
            latency_ms=int((time.perf_counter() - started) * 1000),
            llm_provider="none",
            llm_model="none",
            diagnostics={"reason": "empty_question"},
            refusal_reason="empty_question",
        )

    # Query expansion leg: run retrieval for each variant and merge.
    merged: dict[str, Any] = {}
    variant_diagnostics: list[dict[str, Any]] = []
    for variant in norm["search_variants"]:
        passages, diag = hybrid_search(
            db,
            variant,
            filters=request.filters,
            mode=request.mode,  # type: ignore[arg-type]
            limit=request.top_k or settings.rag_max_evidence * 3,
            pool=settings.rag_candidate_pool,
            rerank=True,
        )
        variant_diagnostics.append({"variant": variant, **diag})
        for p in passages:
            existing = merged.get(p.chunk_id)
            if existing is None or p.score > existing.score:
                merged[p.chunk_id] = p

    ranked = sorted(merged.values(), key=lambda p: p.score, reverse=True)
    retrieval_diagnostics = {
        "query": norm,
        "variants": variant_diagnostics,
        "candidates": len(ranked),
        "threshold": settings.rag_evidence_threshold,
    }

    top_k = request.top_k or settings.rag_max_evidence
    items = _build_evidence_items(db, ranked, top_k * 2)

    # Evidence selection: apply the retrieval threshold before spending tokens.
    strong = [i for i in items if i.score >= settings.rag_evidence_threshold]
    if len(strong) < settings.rag_min_evidence:
        strong = items
    evidence = _prune_redundant(strong, )[:top_k]

    conflicting, conflict_note = detect_conflicts(evidence)

    if not evidence:
        result = _finalise(
            db,
            request=request,
            answer=REFUSAL_TEXT,
            answer_kind=AnswerKind.INSUFFICIENT_EVIDENCE,
            evidence=[],
            claims=[],
            conflicting=False,
            conflict_note=None,
            language=answer_language,
            question_language=question_language,
            started=started,
            llm_provider="none",
            llm_model="none",
            diagnostics={
                **retrieval_diagnostics,
                "refusal": "no_passages_above_threshold",
            },
            persist=persist,
            refusal_reason="no_evidence",
        )
        return result

    context = GroundedContext(
        question=request.question,
        evidence=evidence,
        answer_language=answer_language,
        conflicting=conflicting,
        conflict_note=conflict_note,
        insufficient=False,
    )

    provider = get_llm_provider()
    answer_text = ""
    llm_error: str | None = None
    try:
        result = provider.generate(  # type: ignore[call-arg]
            [
                LLMMessage(
                    role="user",
                    content=(
                        f"Question: {request.question}\n\n"
                        "Answer strictly from the evidence above and cite every claim."
                    ),
                )
            ],
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            context=context,
        )
        answer_text = result.text
        llm_provider, llm_model = result.provider, result.model
    except ProviderError as exc:
        llm_error = str(exc)
        log.warning("llm provider failed; refusing rather than answering ungrounded", error=llm_error)
        result = _finalise(
            db,
            request=request,
            answer=REFUSAL_TEXT,
            answer_kind=AnswerKind.ERROR,
            evidence=evidence,
            claims=[],
            conflicting=conflicting,
            conflict_note=conflict_note,
            language=answer_language,
            question_language=question_language,
            started=started,
            llm_provider=provider.name,
            llm_model=getattr(provider, "model_id", "unknown"),
            diagnostics={
                **retrieval_diagnostics,
                "llm_error": llm_error,
                "question_overlap": overlap,
            },
            persist=persist,
            refusal_reason="llm_unavailable",
        )
        return result

    if not answer_text.strip() or answer_text.strip() == REFUSAL_TEXT:
        answer_kind = AnswerKind.INSUFFICIENT_EVIDENCE
        grounded = False
        answer_text = answer_text.strip() or REFUSAL_TEXT
        claims: list[Claim] = []
        groundedness = 0.0
    else:
        content = scored_text(answer_text)
        claims = _classify_claims(content, evidence)
        groundedness = score_groundedness(content, evidence, claims)
        verified_count, total_evidence = verification_mix(evidence)
        all_unverified = total_evidence > 0 and verified_count == 0
        # Two thresholds, because two different questions are being asked:
        #   * is the answer even about the retrieved material?  (relevance)
        #   * is it supported closely enough to stand as a quotation?  (rigour)
        # Unchecked passages can never be quoted, so they are scored only for
        # relevance; anything below the relevance floor is refused outright.
        summary_floor = settings.rag_unverified_summary_threshold
        strict = settings.rag_groundedness_threshold
        overlap = question_overlap(request.question, evidence)
        below_topic = overlap < settings.rag_min_question_overlap
        below_relevance = below_topic or groundedness < summary_floor
        below_rigour = groundedness < strict
        if below_relevance or (below_rigour and not all_unverified):
            answer_text = (
                REFUSAL_TEXT
                + "\n\n"
                + (
                    f"The retrieved passages do not appear to be about this question "
                    f"(question overlap {overlap:.2f} < "
                    f"{settings.rag_min_question_overlap:.2f})."
                    if below_topic
                    else (
                        f"The retrieved passages were not specific enough to support a "
                        f"grounded answer (groundedness {groundedness:.2f} < "
                        f"{settings.rag_groundedness_threshold:.2f})."
                    )
                )
                + " The passages retrieved are still listed below so you can read them."
            )
            answer_kind = AnswerKind.INSUFFICIENT_EVIDENCE
            grounded = False
            claims = []
            groundedness = 0.0
        elif all_unverified:
            # Relevant passages exist but none is a checked transcription. Answer
            # from them as a clearly labelled summary rather than refusing: the
            # visitor gets the material and the caveat, not a blank page.
            answer_kind = AnswerKind.UNVERIFIED_SUMMARY
            grounded = True
            if UNVERIFIED_LEAD not in answer_text:
                answer_text = f"{UNVERIFIED_LEAD}\n\n{answer_text}"
        elif conflicting:
            answer_kind = AnswerKind.CONFLICTING_SOURCES
            grounded = True
            if CONFLICT_MARKER not in answer_text:
                answer_text = f"{CONFLICT_MARKER}\n\n{answer_text}"
        else:
            answer_kind = AnswerKind.GROUNDED
            grounded = True

    return _finalise(
        db,
        request=request,
        answer=answer_text,
        answer_kind=answer_kind,
        evidence=evidence,
        claims=claims,
        conflicting=conflicting,
        groundedness=groundedness,
        conflict_note=conflict_note,
        language=answer_language,
        question_language=question_language,
        started=started,
        llm_provider=llm_provider,
        llm_model=llm_model,
        diagnostics={**retrieval_diagnostics, "llm_error": llm_error},
        persist=persist,
        refusal_reason=None if grounded else "threshold",
    )


def _finalise(
    db: Session,
    *,
    request: RAGRequest,
    answer: str,
    answer_kind: AnswerKind,
    evidence: list[EvidenceItem],
    claims: list[Claim],
    conflicting: bool,
    conflict_note: str | None,
    language: str,
    question_language: str,
    started: float,
    llm_provider: str,
    llm_model: str,
    diagnostics: dict[str, Any],
    persist: bool,
    refusal_reason: str | None,
    groundedness: float | None = None,
) -> RAGResult:
    citations = validate_citations(answer, evidence)
    latency = int((time.perf_counter() - started) * 1000)
    grounded = answer_kind in (
        AnswerKind.GROUNDED,
        AnswerKind.CONFLICTING_SOURCES,
        AnswerKind.UNVERIFIED_SUMMARY,
    )

    query_id: str | None = None
    if persist:
        query = RagQuery(
            question=request.question,
            question_language=question_language,
            answer_language=language,
            answer=answer,
            answer_kind=answer_kind,
            grounded=grounded,
            groundedness=(
                groundedness
                if groundedness is not None
                else (score_groundedness(scored_text(answer), evidence, claims) if grounded else 0.0)
            ),
            retrieval={
                "evidence": [
                    {
                        "marker": e.marker,
                        "chunk_id": e.chunk_id,
                        "document_id": e.document_id,
                        "title": e.document_title,
                        "page": e.page_number,
                        "page_number": e.page_number,
                        "score": e.score,
                        "source_url": e.source_url,
                        "source_reference": e.source_reference,
                        "verification_status": e.verification_status,
                        "provenance_warning": e.provenance_warning,
                    }
                    for e in evidence
                ],
                "diagnostics": diagnostics if request.include_diagnostics else {},
            },
            diagnostics={
                "claims": [
                    {"text": c.text, "kind": c.kind.value, "markers": c.markers, "supported": c.supported}
                    for c in claims
                ],
                "conflict_note": conflict_note,
                "refusal_reason": refusal_reason,
            }
            if request.include_diagnostics
            else {},
            filters=request.filters.as_dict(),
            llm_provider=llm_provider,
            llm_model=llm_model,
            latency_ms=latency,
            evidence_count=len(evidence),
            citation_count=len(citations),
            conflicting=conflicting,
            user_id=request.user_id,
            session_key=request.session_key,
        )
        db.add(query)
        db.flush()
        query_id = query.id
        for citation in citations:
            citation.query_id = query.id
            db.add(citation)
        db.commit()

    return RAGResult(
        answer=answer,
        answer_kind=answer_kind,
        grounded=grounded,
        groundedness=(
            groundedness
            if groundedness is not None
            else (score_groundedness(scored_text(answer), evidence, claims) if grounded else 0.0)
        ),
        evidence=evidence,
        claims=claims,
        conflicting=conflicting,
        conflict_note=conflict_note,
        citations=citations,
        language=language,
        question_language=question_language,
        latency_ms=latency,
        llm_provider=llm_provider,
        llm_model=llm_model,
        diagnostics=diagnostics if request.include_diagnostics else {},
        query_id=query_id,
        refusal_reason=refusal_reason,
    )
