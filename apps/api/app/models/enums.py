"""Controlled vocabularies shared by the ORM, the API schemas and the UI.

These values are also exported to TypeScript (``packages/types``) so the front
end never hard-codes strings that the backend owns.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ARCHIVIST = "ARCHIVIST"
    EDITOR = "EDITOR"
    RESEARCHER = "RESEARCHER"
    VIEWER = "VIEWER"


class DocumentType(StrEnum):
    BOOK = "book"
    MANUSCRIPT = "manuscript"
    SPEECH = "speech"
    ARTICLE = "article"
    LETTER = "letter"
    DEBATE = "debate"
    PHOTOGRAPH = "photograph"
    AUDIO = "audio"
    VIDEO = "video"
    NEWSPAPER = "newspaper"
    ARCHIVAL_RECORD = "archival_record"


class LanguageCode(StrEnum):
    EN = "en"
    HI = "hi"
    MR = "mr"
    GU = "gu"
    PA = "pa"
    BN = "bn"
    TA = "ta"
    TE = "te"
    KN = "kn"
    ML = "ml"
    OR = "or"
    UR = "ur"
    PALI = "pi"


class ProcessingState(StrEnum):
    UPLOADED = "uploaded"
    VALIDATED = "validated"
    EXTRACTING = "extracting"
    OCR = "ocr"
    AWAITING_REVIEW = "awaiting_review"
    METADATA = "metadata"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    GRAPH = "graph"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"


class OcrStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    PROCESSING = "processing"
    REVIEW = "review"
    APPROVED = "approved"
    FAILED = "failed"


class IndexStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    STALE = "stale"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class JobKind(StrEnum):
    INGEST = "ingest"
    EXTRACT_TEXT = "extract_text"
    OCR = "ocr"
    POST_OCR_CORRECTION = "post_ocr_correction"
    METADATA = "metadata"
    CHUNK = "chunk"
    EMBED = "embed"
    GRAPH = "graph"
    PUBLISH = "publish"
    FINALISE = "finalise"
    TRANSCRIBE_MEDIA = "transcribe_media"
    TRANSLATE = "translate"
    INTEGRITY_CHECK = "integrity_check"


class EntityType(StrEnum):
    PERSON = "PERSON"
    DOCUMENT = "DOCUMENT"
    BOOK = "BOOK"
    SPEECH = "SPEECH"
    MANUSCRIPT = "MANUSCRIPT"
    EVENT = "EVENT"
    DATE = "DATE"
    PLACE = "PLACE"
    ORGANIZATION = "ORGANIZATION"
    COMMITTEE = "COMMITTEE"
    CONSTITUTIONAL_ARTICLE = "CONSTITUTIONAL_ARTICLE"
    TOPIC = "TOPIC"
    COLLECTION = "COLLECTION"
    PHOTOGRAPH = "PHOTOGRAPH"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"


class RelationType(StrEnum):
    AUTHORED = "AUTHORED"
    SPOKE_IN = "SPOKE_IN"
    MENTIONS = "MENTIONS"
    RELATED_TO = "RELATED_TO"
    PARTICIPATED_IN = "PARTICIPATED_IN"
    MEMBER_OF = "MEMBER_OF"
    OCCURRED_AT = "OCCURRED_AT"
    OCCURRED_ON = "OCCURRED_ON"
    DISCUSSES = "DISCUSSES"
    REFERENCES = "REFERENCES"
    PRECEDED = "PRECEDED"
    FOLLOWED = "FOLLOWED"
    HAS_SOURCE = "HAS_SOURCE"
    HAS_MEDIA = "HAS_MEDIA"
    BELONGS_TO = "BELONGS_TO"
    RELATED_TO_ARTICLE = "RELATED_TO_ARTICLE"


class VerificationStatus(StrEnum):
    """How far a text has been checked against the original source.

    This is deliberately separate from :class:`OcrStatus`. A page can have
    accurate OCR (ocr_status=approved) and still be a modern paraphrase that was
    never compared with the archival document. Quoting such text as a primary
    source would be a lie, so the two axes must never be collapsed into one.
    """

    VERIFIED_PRIMARY = "verified_primary"
    """Text checked character-by-character against a preserved original."""

    UNVERIFIED_SECONDARY = "unverified_secondary"
    """A third-party summary, transcription or retelling. Citable as a summary only."""

    MACHINE_TRANSLATION = "machine_translation"
    """A model rendering of another language text. Never a source of record."""

    PENDING_REVIEW = "pending_review"
    """Not yet checked by an archivist."""


class SourceTier(StrEnum):
    """Reliability ordering used as a ranking signal (see §69 of the brief)."""

    GOVERNMENT_PRIMARY = "government_primary"
    INSTITUTIONAL_ARCHIVE = "institutional_archive"
    SCHOLARLY = "scholarly"
    SECONDARY = "secondary"
    UNKNOWN = "unknown"


class MediaKind(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"
    IMAGE = "image"


class ClaimKind(StrEnum):
    """How a sentence in a research answer relates to the evidence."""

    DIRECT_QUOTE = "direct_quote"
    PARAPHRASE = "paraphrase"
    ARCHIVAL_FACT = "archival_fact"
    SECONDARY_INTERPRETATION = "secondary_interpretation"


class AnswerKind(StrEnum):
    GROUNDED = "grounded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_SOURCES = "conflicting_sources"
    UNVERIFIED_SUMMARY = "unverified_summary"
    """Relevant material was found, but none of it has been checked against an
    original. The answer is presented as a summary of the cited sources and is
    never attributed to the speaker as a quotation."""
    ERROR = "error"
