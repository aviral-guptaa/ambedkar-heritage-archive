"""Provider interfaces.

Every AI, storage, graph and job capability is expressed as a Protocol plus a
small result dataclass. Concrete implementations live in sibling modules and are
selected by environment variables — nothing in the application imports a vendor
SDK directly.

A provider that is not installed/configured must raise ``ProviderUnavailable`` so
the API can return an explicit, honest capability state instead of pretending to
work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# exceptions
# --------------------------------------------------------------------------- #


class ProviderError(RuntimeError):
    """A provider was reachable but failed."""


class ProviderUnavailable(ProviderError):
    """A provider is not installed or not configured on this deployment."""


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class LLMMessage:
    role: str  # system | user | assistant
    content: str


@dataclass(slots=True)
class LLMResult:
    text: str
    provider: str
    model: str
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 900,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> LLMResult: ...


# --------------------------------------------------------------------------- #
# embeddings
# --------------------------------------------------------------------------- #


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    dimension: int
    model_id: str

    def is_available(self) -> bool: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# --------------------------------------------------------------------------- #
# reranking
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class RerankItem:
    index: int
    score: float
    text: str = ""


@runtime_checkable
class RerankerProvider(Protocol):
    name: str
    model_id: str

    def is_available(self) -> bool: ...

    def rerank(self, query: str, documents: list[str], top_k: int = 10) -> list[RerankItem]: ...


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class OCRBlock:
    text: str
    confidence: float
    box: list[list[float]]
    language: str | None = None
    block_type: str | None = None


@dataclass(slots=True)
class OCRResult:
    text: str
    confidence: float
    engine: str
    engine_version: str | None
    language: str | None
    blocks: list[OCRBlock] = field(default_factory=list)
    layout: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


@runtime_checkable
class OCRProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def languages(self) -> list[str]: ...

    def recognize(self, image_bytes: bytes, language: str | None = None) -> OCRResult: ...


# --------------------------------------------------------------------------- #
# speech to text
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class STTResult:
    text: str
    language: str | None
    confidence: float | None
    provider: str
    model: str | None = None
    duration_ms: int = 0
    segments: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class STTProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> STTResult: ...


# --------------------------------------------------------------------------- #
# text to speech
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class TTSResult:
    audio: bytes
    mime_type: str
    provider: str
    model: str | None = None
    duration_ms: int = 0


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def supports(self, language: str) -> bool: ...

    def synthesize(self, text: str, language: str) -> TTSResult: ...


# --------------------------------------------------------------------------- #
# translation
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class TranslationResult:
    text: str
    source_language: str
    target_language: str
    provider: str
    model: str | None = None
    duration_ms: int = 0


@runtime_checkable
class TranslationProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def translate(self, text: str, source_language: str, target_language: str) -> TranslationResult: ...


# --------------------------------------------------------------------------- #
# object storage
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class StoredObject:
    key: str
    size: int
    content_type: str
    sha256: str
    etag: str | None = None
    last_modified: str | None = None
    stream_url: str | None = None


@runtime_checkable
class ObjectStore(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def open_stream(self, key: str):  # noqa: ANN201 - file-like
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> bool: ...

    def stat(self, key: str) -> StoredObject | None: ...

    def url_for(self, key: str, expires_seconds: int = 900) -> str: ...


# --------------------------------------------------------------------------- #
# knowledge graph
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class GraphNodeData:
    id: str
    label: str
    entity_type: str
    name: str
    year: int | None = None
    summary: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    degree: int = 0


@dataclass(slots=True)
class GraphEdgeData:
    id: str
    source: str
    target: str
    relation: str
    weight: float = 1.0
    confidence: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphSubgraph:
    nodes: list[GraphNodeData]
    edges: list[GraphEdgeData]
    backend: str
    truncated: bool = False


@dataclass(slots=True)
class GraphStats:
    backend: str
    node_count: int
    edge_count: int
    nodes_by_type: dict[str, int]
    relations_by_type: dict[str, int]


@runtime_checkable
class GraphStore(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def upsert_node(self, node: GraphNodeData) -> None: ...

    def upsert_edge(self, edge: GraphEdgeData) -> None: ...

    def neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 1,
        limit: int = 40,
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> GraphSubgraph: ...

    def search_nodes(self, query: str, limit: int = 20) -> list[GraphNodeData]: ...

    def stats(self) -> GraphStats: ...

    def path_between(self, source: str, target: str, max_depth: int = 4) -> GraphSubgraph: ...


# --------------------------------------------------------------------------- #
# job queue
# --------------------------------------------------------------------------- #


@runtime_checkable
class JobQueue(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def enqueue(self, job_id: str) -> None: ...

    def dequeue(self, timeout: int = 5) -> str | None: ...

    def queue_depth(self) -> int: ...

    def workers_alive(self) -> int: ...


# --------------------------------------------------------------------------- #
# capability reporting
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Capability:
    key: str
    label: str
    available: bool
    detail: str
    provider: str | None = None
    model: str | None = None
