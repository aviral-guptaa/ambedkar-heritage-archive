"""LLM providers.

Three implementations:

* ``extractive`` (default) — a deterministic, *guaranteed grounded* composer.
  It selects sentences from the retrieved evidence with a lexical scorer and
  emits them verbatim with citation markers. Because the answer text is copied
  from indexed passages, it is structurally incapable of inventing a quote,
  date or page number. It is the honest fallback for an offline kiosk and the
  default for the SIH demo.
* ``openai_compatible`` — any OpenAI ``/chat/completions`` endpoint. Works
  unchanged against llama.cpp's server, LM Studio, vLLM, Ollama's compat mode,
  or a hosted provider. No vendor is hard-coded.
* ``ollama`` — native Ollama ``/api/chat``.

All generative providers receive a strict grounding system prompt and the same
``GroundedContext``; citation validation runs after generation regardless of
provider, so a misbehaving model cannot inject unverified citations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import (
    LLMMessage,
    LLMProvider,
    LLMResult,
    ProviderError,
    ProviderUnavailable,
)
from app.providers.embedding import _content_tokens

log = get_logger(__name__)

GROUNDING_SYSTEM_PROMPT = """\
You are the Evidence-Grounded Research Assistant of a national digital heritage \
archive dedicated to Dr. B. R. Ambedkar.

ABSOLUTE RULES
1. Answer ONLY from the EVIDENCE passages supplied below. You have no other \
knowledge of this archive.
2. Never invent quotations, dates, page numbers, volumes, document titles, \
speeches, constitutional article numbers, events or URLs. If a fact is not in \
the evidence, it does not exist for you.
3. If the evidence does not answer the question, reply with exactly: \
"I could not find sufficient verified evidence in the indexed archive to answer this question."
4. Cite every claim with the evidence markers, e.g. [1], [2]. Multiple markers are allowed.
5. When a page number is not present in the evidence, write \
"Page information unavailable in indexed source." Do not guess.
6. If two evidence passages give differing accounts or wording, say \
"Sources contain differing accounts/wording." and present both. Do not silently pick one.
7. Prefer the phrasing of the archival record. Introduce claims as archival facts, \
e.g. "In the indexed Constituent Assembly debate dated 25 November 1949, ..." — \
never assert personal belief ("Ambedkar believed...") unless the evidence says so.
8. Answer in the requested language. Keep quoted evidence in its original language \
and gloss it in the answer language.
9. Plain prose or short markdown. No headings, no bullet spam, no preamble.

EVIDENCE
{evidence}
"""

REFUSAL_TEXT = (
    "I could not find sufficient verified evidence in the indexed archive to answer this question."
)

CONFLICT_MARKER = "Sources contain differing accounts/wording."


@dataclass(slots=True)
class EvidenceItem:
    """One retrieved passage plus everything needed to cite it honestly."""

    marker: int
    text: str
    document_title: str
    document_id: str | None = None
    chunk_id: str | None = None
    date_label: str | None = None
    speaker: str | None = None
    page_number: int | None = None
    section: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    source_reference: str | None = None
    source_tier: str = "unknown"
    document_type: str | None = None
    collection: str | None = None
    language: str = "en"
    verification_status: str = "verified_primary"
    quote_verified: bool = True
    provenance_warning: str | None = None
    score: float = 0.0
    vector_score: float = 0.0
    keyword_score: float = 0.0
    rerank_score: float = 0.0
    rank_vector: int | None = None
    rank_keyword: int | None = None

    def as_citation_block(self) -> str:
        bits = [f"[{self.marker}] {self.document_title}"]
        if self.date_label:
            bits.append(f"Date: {self.date_label}")
        if self.speaker:
            bits.append(f"Speaker/Author: {self.speaker}")
        if self.source_name:
            bits.append(f"Source: {self.source_name}")
        if self.page_number is not None:
            bits.append(f"Page: {self.page_number}")
        else:
            bits.append("Page information unavailable in indexed source.")
        if self.section:
            bits.append(f"Section: {self.section}")
        if self.source_reference:
            bits.append(f"Reference: {self.source_reference}")
        header = "\n   ".join(bits)
        return f"{header}\n   \"{self.text.strip()}\""


@dataclass(slots=True)
class GroundedContext:
    question: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    answer_language: str = "en"
    conflicting: bool = False
    conflict_note: str | None = None
    insufficient: bool = False

    def as_prompt_block(self) -> str:
        return "\n\n".join(item.as_citation_block() for item in self.evidence)


# --------------------------------------------------------------------------- #
# extractive (default)
# --------------------------------------------------------------------------- #

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।॥])\s+|\n{2,}")
_WS = re.compile(r"\s+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text or "") if p and p.strip()]
    cleaned: list[str] = []
    for p in parts:
        if len(p) < 25 and cleaned:
            cleaned[-1] = f"{cleaned[-1]} {p}".strip()
            continue
        cleaned.append(p)
    return cleaned


class ExtractiveGroundedProvider:
    """Answer composed only by quoting retrieved evidence."""

    name = "extractive"
    model_id = "extractive-grounded-v1"

    def is_available(self) -> bool:
        return True

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _sentence_scores(question: str, sentences: list[str]) -> list[float]:
        q = set(_content_tokens(question))
        if not q:
            return [0.0] * len(sentences)
        counts = {t: sum(1 for s in sentences if t in _content_tokens(s)) for t in q}
        n = len(sentences)
        import math

        scores: list[float] = []
        for s in sentences:
            st = _content_tokens(s)
            if not st:
                scores.append(0.0)
                continue
            overlap = sum(1 for t in q if t in st)
            idf = sum(
                math.log(1 + n / (1 + counts[t])) for t in q if t in st
            ) / max(1e-9, sum(math.log(1 + n / (1 + counts[t])) for t in q) or 1e-9)
            # prefer sentences in the right size band: 60-320 chars
            length_penalty = 1.0 if 60 <= len(s) <= 320 else 0.82
            coverage = overlap / len(q)
            scores.append((0.65 * idf + 0.35 * coverage) * length_penalty)
        return scores

    def _compose(self, context: GroundedContext) -> str:
        if context.insufficient or not context.evidence:
            return REFUSAL_TEXT

        q_tokens = set(_content_tokens(context.question))
        best: list[tuple[int, float, str, int]] = []  # (item order, score, sentence, marker)
        for item in context.evidence:
            sents = split_sentences(item.text)
            scores = self._sentence_scores(context.question, sents)
            for order, (s, sc) in enumerate(zip(sents, scores)):
                if sc <= 0:
                    continue
                # Small prior for early sentences of a passage.
                sc *= 1.0 + 0.04 * max(0, 3 - order)
                best.append((item.marker, sc, s.strip(), item.marker))

        if not best:
            # No lexical overlap at all: quote the leading sentence of the single
            # best passage so the user still sees the real record.
            top = max(context.evidence, key=lambda e: e.score)
            sents = split_sentences(top.text)
            quote = sents[0] if sents else top.text.strip()
            return self._frame(top, [quote])

        best.sort(key=lambda r: r[1], reverse=True)
        chosen: list[tuple[int, str]] = []
        per_marker: dict[int, int] = {}
        for marker, _score, sentence, _m in best:
            if per_marker.get(marker, 0) >= 3:
                continue
            st = set(_content_tokens(sentence))
            # avoid repeating near-identical sentences
            if any(st and len(st & set(_content_tokens(c))) / max(1, len(st | set(_content_tokens(c)))) > 0.72 for _m, c in chosen):
                continue
            chosen.append((marker, sentence))
            per_marker[marker] = per_marker.get(marker, 0) + 1
            if len(chosen) >= 5:
                break

        if not chosen:
            return REFUSAL_TEXT

        # Rebuild in evidence order so the answer reads like the archive.
        chosen.sort(key=lambda r: r[0])
        by_marker = {e.marker: e for e in context.evidence}
        checked_flags = {
            marker: bool(by_marker[marker].quote_verified)
            and by_marker[marker].verification_status == "verified_primary"
            for marker, _ in chosen
        }
        lines: list[str] = []
        if all(checked_flags.values()):
            for marker, sentence in chosen:
                lines.append(
                    f'{self._frame(by_marker[marker], None, inline=True)} “{sentence}” [{marker}]'
                )
        else:
            # One heading per source, then its points as a list. Repeating the
            # full attribution before every sentence buries the content, and a
            # heading makes the unchecked status obvious before the reader starts
            # treating these points as fact.
            lines.append(
                "The indexed material below makes the following points. They are a "
                "summary of the cited records, not quotations:"
            )
            group: list[tuple[str, list[tuple[str, str]]]] = []
            for marker, sentence in chosen:
                item = by_marker[marker]
                heading = self._where(item)
                if group and group[-1][0] == heading:
                    group[-1][1].append((sentence, marker))
                else:
                    group.append((heading, [(sentence, marker)]))
            for heading, points in group:
                lines.append(f"**{heading}**")
                lines.append(
                    "\n".join(f"- {sentence} [{marker}]" for sentence, marker in points)
                )
        body = "\n\n".join(lines)
        if context.conflicting:
            body = f"{CONFLICT_MARKER}\n\n{body}"
        return body

    def _where(self, item: EvidenceItem) -> str:
        """Name a passage's source, without claiming anything about its fidelity."""
        where = item.document_title
        if item.date_label:
            where = f"{where} ({item.date_label})"
        if item.speaker:
            where = f"{item.speaker}, {where}"
        return where

    def _frame(self, item: EvidenceItem, quote: str | None, inline: bool = False) -> str:
        """Introduce a retrieved passage.

        Only text whose fidelity to the original has been checked is framed as a
        record of what was said. Unchecked text is framed as a summary of the
        record, because quotation marks are a claim about exact wording that this
        archive cannot make on its own.
        """
        where = self._where(item)
        checked = bool(item.quote_verified) and item.verification_status == "verified_primary"
        if checked:
            label = "In the indexed record" if not item.document_type else "In the indexed"
        else:
            label = "An unverified summary of the indexed record"
        if inline:
            return f"{label} {where}:"
        if not checked:
            # No quotation marks around unchecked text: the record paraphrases its
            # source, so presenting it inside quotes invites the reader to treat a
            # modern retelling as Dr. Ambedkar's own words.
            return f"{label} {where}, which states: {quote}"
        return f"{label} {where}: “{quote}”"

    # -- interface -------------------------------------------------------
    def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 900,
        stop: list[str] | None = None,
        context: GroundedContext | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        if context is None:
            # No structured evidence ⇒ never answer. Refuse.
            return LLMResult(
                text=REFUSAL_TEXT,
                provider=self.name,
                model=self.model_id,
                finish_reason="refused",
            )
        text = self._compose(context)
        text = _WS.sub(" ", text).strip() if "\n" not in text else text.strip()
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model_id,
            finish_reason="stop",
            completion_tokens=len(text.split()),
        )


# --------------------------------------------------------------------------- #
# HTTP providers
# --------------------------------------------------------------------------- #


class _HTTPChatProvider:
    name = "http"
    model_id = "unset"

    def _payload_messages(
        self, messages: list[LLMMessage], context: GroundedContext | None
    ) -> list[dict[str, str]]:
        if context is None:
            return [{"role": m.role, "content": m.content} for m in messages]
        system = GROUNDING_SYSTEM_PROMPT.format(evidence=context.as_prompt_block())
        language = context.answer_language
        user = (
            f"{messages[-1].content}\n\n"
            f"Answer in this language: {language}. "
            "If the evidence is insufficient, use the exact refusal sentence given in the rules."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _finish(self, data: dict[str, Any]) -> LLMResult:
        choices = data.get("choices") or []
        text = ""
        finish = "stop"
        if choices:
            message = choices[0].get("message") or {}
            text = message.get("content") or ""
            finish = choices[0].get("finish_reason") or "stop"
        usage = data.get("usage") or {}
        return LLMResult(
            text=text.strip(),
            provider=self.name,
            model=data.get("model") or self.model_id,
            finish_reason=finish,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            raw={"id": data.get("id")},
        )


class OpenAICompatibleProvider(_HTTPChatProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.model_id = settings.llm_model
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key

    def is_available(self) -> bool:
        return bool(self.base_url) and bool(self.model_id)

    def health(self) -> tuple[bool, str]:
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(url, headers=headers)
            return (r.status_code < 400, f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            return (False, str(exc)[:120])

    def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 900,
        stop: list[str] | None = None,
        context: GroundedContext | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        if not self.is_available():
            raise ProviderUnavailable("LLM_BASE_URL / LLM_MODEL are not configured.")
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": self._payload_messages(messages, context),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if stop:
            body["stop"] = stop
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/chat/completions"
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                r = client.post(url, json=body, headers=headers)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"LLM endpoint returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"LLM endpoint unreachable: {exc}") from exc
        return self._finish(data)


class OllamaProvider(_HTTPChatProvider):
    name = "ollama"

    def __init__(self) -> None:
        self.model_id = settings.llm_model
        self.base_url = settings.llm_base_url.rstrip("/")

    def is_available(self) -> bool:
        return bool(self.base_url) and bool(self.model_id)

    def health(self) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self.base_url}/api/tags")
            if r.status_code < 400:
                tags = r.json().get("models") or []
                names = [t.get("name") for t in tags]
                if self.model_id in names or any(
                    str(n).startswith(self.model_id.split(":")[0]) for n in names if n
                ):
                    return (True, f"model {self.model_id} present")
                return (False, f"model {self.model_id} not pulled (have: {names[:5]})")
            return (False, f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            return (False, str(exc)[:120])

    def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 900,
        stop: list[str] | None = None,
        context: GroundedContext | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        if not self.is_available():
            raise ProviderUnavailable("LLM_BASE_URL / LLM_MODEL are not configured.")
        payload_messages = self._payload_messages(messages, context)
        body = {
            "model": self.model_id,
            "messages": payload_messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if stop:
            body["options"]["stop"] = stop
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                r = client.post(f"{self.base_url}/api/chat", json=body)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Ollama endpoint unreachable: {exc}") from exc
        message = data.get("message") or {}
        return LLMResult(
            text=str(message.get("content") or "").strip(),
            provider=self.name,
            model=self.model_id,
            finish_reason="stop",
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
        )


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    choice = settings.llm_provider
    if choice == "openai_compatible":
        return OpenAICompatibleProvider()
    if choice == "ollama":
        return OllamaProvider()
    return ExtractiveGroundedProvider()


def llm_capability() -> dict[str, Any]:
    provider = get_llm_provider()
    detail = (
        "Extractive grounded composer. Answers are quoted from retrieved evidence, "
        "so they cannot contain invented facts. Configure LLM_PROVIDER=ollama or "
        "openai_compatible for a generative model."
        if isinstance(provider, ExtractiveGroundedProvider)
        else f"{provider.name} / {getattr(provider, 'model_id', '')}"
    )
    if hasattr(provider, "health"):
        ok, msg = provider.health()  # type: ignore[attr-defined]
        if not ok:
            detail = f"Configured but unreachable: {msg}. Falling back is handled per-request."
    return {
        "available": True,
        "provider": provider.name,
        "model": getattr(provider, "model_id", "n/a"),
        "detail": detail,
        "generative": not isinstance(provider, ExtractiveGroundedProvider),
    }
