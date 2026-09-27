"""Embedding providers.

Two implementations ship:

* ``sentence_transformers`` — the default when the optional dependency and
  model weights are present. Best semantic quality.
* ``hashing`` — a dependency-free, fully deterministic fallback: signed
  feature hashing over word unigrams/bigrams and character 3–5-grams, weighted
  by sub-linear term frequency, L2-normalised. It is a genuine lexical/semantic
  vector (not a stub) and keeps the offline kiosk path working with zero model
  downloads. Reported honestly in the capability endpoint.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from functools import lru_cache

import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import EmbeddingProvider, ProviderUnavailable

log = get_logger(__name__)

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are", "was",
    "were", "be", "by", "with", "that", "this", "it", "as", "at", "from", "which",
    "he", "she", "they", "we", "you", "i", "but", "not", "has", "have", "had",
    # Interrogatives, auxiliaries and pronouns: these appear in almost any
    # question, so counting them as "content" makes unrelated questions look
    # relevant to whatever the archive happens to hold.
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "did", "does", "do", "done", "will", "would", "can", "could", "should",
    "shall", "may", "might", "must", "there", "their", "them", "his", "her",
    "its", "our", "your", "my", "me", "him", "us", "been", "being", "am",
    "say", "says", "said", "tell", "told", "about", "into", "over", "under",
    "और", "का", "की", "के", "को", "में", "से", "पर", "है", "था", "थे", "थी", "यह",
    "वह", "एक", "लिए", "कि", "था।", "मा", "क्या", "कौन", "कब", "क्यों", "कैसे",
}


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text)]


def _content_tokens(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in _STOPWORDS and len(t) > 1]


def _char_ngrams(text: str, low: int = 3, high: int = 5) -> list[str]:
    padded = f" {text.lower().strip()} "
    grams: list[str] = []
    for n in range(low, high + 1):
        for i in range(max(0, len(padded) - n + 1)):
            grams.append(padded[i : i + n])
    return grams


def _bucket(feature: str, dim: int, salt: str) -> tuple[int, float]:
    digest = hashlib.blake2b(f"{salt}:{feature}".encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % dim
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


class HashingEmbeddingProvider:
    """Deterministic hashed-feature embedder. No network, no model download."""

    name = "hashing"
    # The id encodes the tokenisation, not just the algorithm: a change to the
    # stopword list makes every existing vector stale, and the version suffix is
    # what makes rebuild_embeddings() find them.
    model_id = "dha-hashing-v2"

    def __init__(self, dimension: int | None = None) -> None:
        self.dimension = dimension or settings.embedding_dim

    def is_available(self) -> bool:
        return True

    def _embed_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dimension, dtype=np.float32)
        text = (text or "").strip()
        if not text:
            return vec
        words = _tokens(text)
        content = _content_tokens(text)
        # word unigrams
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        for token, count in counts.items():
            weight = 1.0 + math.log(count)  # sub-linear tf
            if token in _STOPWORDS:
                weight *= 0.25
            idx, sign = _bucket(f"w:{token}", self.dimension, "w1")
            vec[idx] += sign * weight
        # word bigrams capture a little word order
        for a, b in zip(words, words[1:]):
            idx, sign = _bucket(f"b:{a}_{b}", self.dimension, "w2")
            vec[idx] += sign * 0.6
        # character n-grams give robustness to morphology + OCR noise
        for gram in _char_ngrams(text):
            idx, sign = _bucket(f"c:{gram}", self.dimension, "c1")
            vec[idx] += sign * 0.12
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t).tolist() for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text).tolist()


class SentenceTransformerEmbeddingProvider:
    name = "sentence_transformers"

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or settings.embedding_model
        self._model = None
        self._lock = threading.Lock()
        self._init_error: str | None = None

    def is_available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    def _ensure(self):  # noqa: ANN202
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_id)
            except Exception as exc:  # noqa: BLE001
                self._init_error = f"sentence-transformers unavailable: {exc}"
                raise ProviderUnavailable(self._init_error) from exc
        return self._model

    @property
    def dimension(self) -> int:  # type: ignore[override]
        try:
            return int(self._ensure().get_sentence_embedding_dimension())
        except ProviderUnavailable:
            return settings.embedding_dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure()
        vectors = model.encode(
            list(texts),
            batch_size=settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    choice = settings.embedding_provider
    if choice == "sentence_transformers":
        st = SentenceTransformerEmbeddingProvider()
        if st.is_available():
            return st
        log.warning(
            "sentence-transformers requested but not importable; using hashing embedder",
            model=settings.embedding_model,
        )
    return HashingEmbeddingProvider()


def embedding_capability() -> dict:
    provider = get_embedding_provider()
    fallback_note = None
    if provider.name == "hashing" and settings.embedding_provider != "hashing":
        fallback_note = (
            f"EMBEDDING_PROVIDER={settings.embedding_provider} requested but the package or "
            "model weights are not present; running the built-in hashing embedder."
        )
    return {
        "available": True,
        "provider": provider.name,
        "model": provider.model_id,
        "dimension": provider.dimension,
        "detail": fallback_note
        or f"{provider.name} / {provider.model_id} / dim {provider.dimension}",
    }
