"""Reranking providers.

``lexical`` is the default and is a real reranker: it fuses the incoming
retrieval signals (fusion score, vector rank, keyword rank), rewards query-term
coverage, penalises lexical redundancy between candidates and adds a small
source-quality prior. ``cross_encoder`` uses a real Cross-Encoder model when the
optional dependency is installed.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import ProviderUnavailable, RerankItem, RerankerProvider
from app.providers.embedding import _content_tokens, _tokens

log = get_logger(__name__)

#: Source reliability prior (brief §69). Applied as a small additive bonus.
TIER_BONUS = {
    "government_primary": 0.06,
    "institutional_archive": 0.04,
    "scholarly": 0.02,
    "secondary": 0.0,
    "unknown": 0.0,
}


class LexicalRerankerProvider:
    name = "lexical"
    model_id = "dha-lexical-reranker-v1"

    def is_available(self) -> bool:
        return True

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
        base_scores: list[float] | None = None,
        source_tiers: list[str] | None = None,
    ) -> list[RerankItem]:
        q_tokens = set(_content_tokens(query))
        if not q_tokens:
            q_tokens = set(_tokens(query))
        scored: list[RerankItem] = []
        seen_token_sets: list[set[str]] = []
        for i, doc in enumerate(documents):
            base = base_scores[i] if base_scores and i < len(base_scores) else 0.0
            d_tokens = set(_content_tokens(doc))
            coverage = (
                len(q_tokens & d_tokens) / len(q_tokens) if q_tokens else 0.0
            )
            density = (
                len(q_tokens & d_tokens) / max(1, len(_tokens(doc))) if q_tokens else 0.0
            )
            phrase = 0.0
            if len(q_tokens) > 1:
                ordered = " ".join(_tokens(doc))
                if " ".join(sorted(q_tokens)) in ordered:
                    phrase = 0.05
            # Redundancy penalty: candidates repeating the same vocabulary add
            # no new information to the evidence set.
            redundancy = 0.0
            for prev in seen_token_sets:
                if not d_tokens or not prev:
                    continue
                jaccard = len(d_tokens & prev) / len(d_tokens | prev)
                redundancy = max(redundancy, jaccard)
            seen_token_sets.append(d_tokens)
            tier = (source_tiers[i] if source_tiers and i < len(source_tiers) else "unknown")
            score = (
                0.55 * base
                + 0.30 * coverage
                + 0.08 * min(1.0, density * 8)
                + phrase
                + TIER_BONUS.get(tier, 0.0)
                - 0.12 * redundancy
            )
            scored.append(RerankItem(index=i, score=round(score, 6), text=doc[:400]))
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]


class CrossEncoderRerankerProvider:
    name = "cross_encoder"

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or settings.reranker_model
        self._model = None
        self._init_error: str | None = None

    def is_available(self) -> bool:
        try:
            import transformers  # noqa: F401
            import torch  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    def _ensure(self):  # noqa: ANN202
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_id)
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"cross-encoder unavailable: {exc}"
            raise ProviderUnavailable(self._init_error) from exc
        return self._model

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
        base_scores: list[float] | None = None,
        source_tiers: list[str] | None = None,
    ) -> list[RerankItem]:
        model = self._ensure()
        pairs = [(query, doc[:2000]) for doc in documents]
        raw = model.predict(pairs, show_progress_bar=False)
        items: list[RerankItem] = []
        for i, score in enumerate(raw):
            value = float(score)
            if math.isnan(value):
                value = 0.0
            items.append(RerankItem(index=i, score=round(value, 6), text=documents[i][:400]))
        items.sort(key=lambda r: r.score, reverse=True)
        return items[:top_k]


class FallbackReranker(LexicalRerankerProvider):
    """Lexical reranker used when a cross-encoder is configured but absent."""

    name = "lexical_fallback"
    model_id = "dha-lexical-reranker-v1"

    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason


@lru_cache(maxsize=1)
def get_reranker_provider() -> RerankerProvider:
    choice = settings.reranker_provider
    if choice == "cross_encoder":
        ce = CrossEncoderRerankerProvider()
        if ce.is_available():
            return ce
        return FallbackReranker(
            f"RERANKER_PROVIDER=cross_encoder requested but the model is not installed "
            f"({settings.reranker_model}); using the lexical reranker."
        )
    return LexicalRerankerProvider()


def rerank_capability() -> dict:
    provider = get_reranker_provider()
    detail = f"{provider.name} / {provider.model_id}"
    if isinstance(provider, FallbackReranker):
        detail = provider.reason
    return {
        "available": True,
        "provider": provider.name,
        "model": provider.model_id,
        "detail": detail,
    }
