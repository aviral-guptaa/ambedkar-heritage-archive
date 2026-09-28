"""Application configuration.

All runtime configuration is environment driven. No secrets live in code.
See ``.env.example`` at the repository root for the documented variable list.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class HardwareConfig(BaseSettings):
    """Abstraction layer for physical kiosk hardware.

    The software never imports a vendor driver directly; capability flags are
    surfaced to the UI through ``GET /api/v1/health`` and
    ``GET /api/v1/system/capabilities`` so the front-end can enable, disable or
    label unavailable peripherals honestly.
    """

    model_config = SettingsConfigDict(env_prefix="HW_", env_file=".env", extra="ignore")

    display_mode: Literal["browser", "kiosk"] = "browser"
    kiosk_mode: bool = False
    scanner_enabled: bool = True
    microphone_enabled: bool = True
    speaker_enabled: bool = True
    camera_enabled: bool = False
    touch_min_target_px: int = 44
    kiosk_idle_timeout_seconds: int = 180
    kiosk_return_home: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app --
    app_name: str = "Digital Heritage Archive API"
    app_slug: str = "dha"
    environment: Literal["development", "testing", "production"] = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"
    public_base_url: str = "http://localhost:8000"
    demo_mode: bool = False
    log_level: str = "INFO"
    log_json: bool = False

    # ----------------------------------------------------------- database --
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/dha"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_echo: bool = False

    # -------------------------------------------------------------- redis --
    redis_url: str = "redis://localhost:6379/0"
    # "inline" runs jobs in the process that enqueued them. It exists because
    # Render's free tier has no background worker, and the alternative on a free
    # tier is a queue nothing ever drains.
    queue_backend: Literal["rq", "database", "inline"] = "database"
    queue_name: str = "dha"

    # -------------------------------------------------------------- neo4j --
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "please-change-me"
    neo4j_database: str = "neo4j"
    graph_backend: Literal["auto", "neo4j", "postgres"] = "auto"

    # -------------------------------------------------------------- minio --
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket: str = "dha-archive"
    storage_backend: Literal["auto", "minio", "filesystem"] = "auto"
    local_storage_path: Path = REPO_ROOT / "var" / "objectstore"

    # ------------------------------------------------------------ security --
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 8
    refresh_token_days: int = 14
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:4173",
            "http://localhost:3000",
        ]
    )
    rate_limit_per_minute: int = 240
    auth_rate_limit_per_minute: int = 15
    max_upload_bytes: int = 512 * 1024 * 1024
    allowed_upload_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
            ".tif",
            ".tiff",
            ".bmp",
            ".webp",
            ".txt",
            ".md",
            ".doc",
            ".docx",
            ".mp3",
            ".wav",
            ".m4a",
            ".ogg",
            ".flac",
            ".mp4",
            ".mov",
            ".webm",
            ".json",
            ".jsonl",
        ]
    )

    # ------------------------------------------------------------- llm ----
    llm_provider: Literal["extractive", "openai_compatible", "ollama"] = "extractive"
    llm_model: str = "extractive-grounded-v1"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""
    llm_temperature: float = 0.0
    llm_max_tokens: int = 900
    llm_timeout_seconds: float = 120.0

    # ------------------------------------------------------- embeddings ----
    embedding_provider: Literal["hashing", "sentence_transformers"] = "hashing"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    embedding_batch_size: int = 32

    # --------------------------------------------------------- reranker ----
    reranker_provider: Literal["lexical", "cross_encoder"] = "lexical"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # --------------------------------------------------------------- ocr ---
    ocr_provider: Literal["auto", "rapidocr", "tesseract", "none"] = "auto"
    ocr_languages: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["eng", "hin", "mar"])
    ocr_default_language: str = "eng"
    #: RapidOCR bundles a Chinese recognition head. Point this at a
    #: Devanagari model before trusting it for Hindi/Marathi scans.
    rapidocr_rec_model: str = ""
    ocr_min_confidence: float = 0.0
    ocr_preprocess: bool = True
    ocr_max_dimension: int = 2600

    # ------------------------------------------------ post-ocr correction --
    post_ocr_correction_provider: Literal["none", "llm"] = "none"
    #: An LLM correction is discarded unless it stays this close to the source.
    ocr_correction_min_similarity: float = 0.90
    ocr_correction_min_length_ratio: float = 0.90
    ocr_correction_max_length_ratio: float = 1.10

    # ------------------------------------------------------- speech / tts --
    stt_provider: Literal["none", "whisper_http", "openai_compatible"] = "none"
    stt_base_url: str = "http://localhost:8080"
    stt_api_key: str = ""
    stt_model: str = "whisper-1"
    stt_languages: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["en", "hi", "mr"])

    tts_provider: Literal["none", "piper", "openai_compatible", "system"] = "none"
    tts_base_url: str = "http://localhost:5000"
    tts_api_key: str = ""
    tts_model: str = "en_IN-x_low"
    tts_voice_map: dict[str, str] = Field(
        default_factory=lambda: {"en": "en_IN-x_low", "hi": "hi_IN-x_low", "mr": "mr_IN-x_low"}
    )

    # ------------------------------------------------------- translation ---
    translation_provider: Literal["none", "llm", "indic_nmt"] = "none"
    translation_model: str = "ai4bharat/indic-translation"

    # ------------------------------------------------------------- chunking -
    chunk_target_chars: int = 1100
    chunk_overlap_chars: int = 160
    chunk_min_chars: int = 180

    # ---------------------------------------------------------------- rag --
    rag_evidence_threshold: float = 0.32
    rag_min_evidence: int = 2
    rag_max_evidence: int = 6
    rag_candidate_pool: int = 60
    rag_groundedness_threshold: float = 0.55
    rag_min_question_overlap: float = 0.34
    """Share of the question's content words that must appear in the evidence.

    Groundedness alone cannot detect an irrelevant question: the answer is built
    by quoting retrieved passages, so it always overlaps them. This gate asks the
    separate question of whether the passages are about the thing asked.
    """

    rag_unverified_summary_threshold: float = 0.15
    """Lower bar for answering from unchecked material.

    Unchecked text is never quoted, so a correct summary scores lower on the
    quotation component of the groundedness measure. It is still worth showing —
    with a provenance warning — rather than refusing outright, as long as the
    words really are supported by the retrieved passages.
    """
    rag_rrf_k: int = 60
    rag_vector_weight: float = 1.0
    rag_keyword_weight: float = 1.0

    # ------------------------------------------------------------- search --
    search_config: str = "english"
    search_default_limit: int = 20
    search_max_limit: int = 100
    search_vector_candidates: int = 80
    search_keyword_candidates: int = 80

    # ---------------------------------------------------------- integrity --
    integrity_auto_check_on_publish: bool = True
    max_page_dimensions: tuple[int, int] = (12000, 12000)

    # ------------------------------------------------------- single port --
    web_dist: Path | None = None
    """Built interface to serve from the API process.

    Left unset, the interface is served by Vite or nginx and the API answers
    only the API. Set it when the platform exposes one port per service, so a
    reader gets the interface and the API from the same origin with no CORS
    configuration to get wrong.
    """

    hardware: HardwareConfig = Field(default_factory=HardwareConfig)

    @field_validator(
        "cors_origins",
        "ocr_languages",
        "stt_languages",
        "allowed_upload_extensions",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        # JSON first. `CORS_ORIGINS=["https://example.org"]` is what everyone
        # writes, and `CORS_ORIGINS=[]` is the natural way to say "none", but
        # `[]` split on commas is the one-element list ["[]"] — a literal
        # origin string that no browser will ever match. The fields carry
        # NoDecode so pydantic-settings leaves the string alone, which means
        # this validator is the only place the format can be handled.
        stripped = v.strip()
        if stripped.startswith(("[", "{")):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                pass  # not JSON after all; fall through to the comma split
            else:
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in v.split(",") if item.strip()]

    @field_validator("database_url", mode="before")
    @classmethod
    def _pin_the_postgres_driver(cls, v: object) -> object:
        """Make a bare ``postgresql://`` URL mean psycopg3.

        SQLAlchemy reads the driver out of the URL scheme, and a URL with no
        ``+driver`` part defaults to psycopg2. This project depends on psycopg3
        and does not install psycopg2, so a bare URL produces
        ``ModuleNotFoundError: No module named 'psycopg2'`` at the first
        connect — which on a fresh Render deploy happened inside
        ``alembic upgrade``, before the application had imported anything of
        its own.

        Nothing caught it here because the local ``.env`` spells the driver out
        as ``postgresql+psycopg://``, and Render's ``fromDatabase`` connection
        string does not: the platform hands you a plain ``postgresql://``, which
        is the most ordinary database URL there is and means something different
        to this code than it looks like it means.

        So the scheme is pinned here, in one place, and every consumer —
        the application's engine, Alembic's online and offline paths — is
        corrected at once. A driver named explicitly is left alone: overriding
        ``postgresql+psycopg2://`` would be second-guessing a deliberate
        choice, and the failure for that is a legible one. ``sqlite://`` is
        untouched, because the unit-test harness uses it.
        """
        if not isinstance(v, str):
            return v
        for bare in ("postgresql://", "postgres://"):
            if v.startswith(bare):
                return "postgresql+psycopg://" + v[len(bare) :]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def storage_root(self) -> Path:
        return Path(self.local_storage_path)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
