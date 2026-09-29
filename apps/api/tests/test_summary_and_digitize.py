"""Tests for the two added features: grounded 30-second summaries, and Digitise.

The rule these tests exist to protect: nothing the archive did not already hold
may appear in a summary, an answer, or a digitisation result. Every assertion
below checks grounding by looking for text that does *not* exist, not only for
text that does.
"""

from __future__ import annotations

import io

import pytest

SAMPLE_TEXT = (
    "It is a truism that in India the caste system has operated as a mechanism for "
    "the subordination of groups. The Brahmans have always claimed their superiority "
    "by the purity of their blood. Endogamy and exogamy are the rules by which caste "
    "is maintained and by which it is reproduced. The abolition of untouchability "
    "requires that the mind of man be made free from the shackles of hereditary "
    "pollution. Untouchability is therefore a sin and the spirit of the Constitution "
    "demands its abolition without delay in every part of the country. Marriage is "
    "therefore the purveyor of caste and it is the primary obstacle to education."
)


@pytest.fixture()
def summarisable(db):
    """A record with enough text to summarise, owned by this test alone.

    The session-scoped ``seeded`` fixture is shared with tests that reindex
    records, which can legitimately clear their chunks, so a summary test that
    depended on it would be at the mercy of test ordering.
    """
    from app.models.archive import Document
    from app.models.enums import (
        DocumentType,
        ProcessingState,
        PublicationStatus,
        VerificationStatus,
    )
    from app.services.ingestion import index_document

    slug = "summary-fixture-record"
    document = db.query(Document).filter(Document.slug == slug).one_or_none()
    if document is None:
        document = Document(
            slug=slug,
            title="Caste in India",
            speaker="Dr. B. R. Ambedkar",
            author_display="Dr. B. R. Ambedkar",
            document_type=DocumentType.SPEECH.value,
            language="en",
            original_language="en",
            year=1916,
            date_precision="year",
            provenance="Created by the summary tests.",
            verification_status=VerificationStatus.UNVERIFIED_SECONDARY,
            checksum_sha256="1" * 64,
            processing_state=ProcessingState.READY,
            publication_status=PublicationStatus.PUBLISHED,
        )
        db.add(document)
        db.flush()
    # Page 0 is the archive's marker for "no pagination".
    index_document(db, document, pages=[(0, SAMPLE_TEXT)])
    db.commit()
    return document.slug


def _png_bytes(text: str = "THE ARCHIVE TEST", width: int = 900, height: int = 260) -> bytes:
    """A real PNG with rendered text, so OCR has something genuine to read."""
    from PIL import Image, ImageDraw

    image = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(image)
    draw.text((30, 60), text, fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# grounded summary
# --------------------------------------------------------------------------- #


def test_summary_quotes_only_the_documents_own_text(summarisable, client):
    response = client.get(f"/api/v1/documents/{summarisable}/summary")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["available"] is True
    assert body["main_idea"]
    assert 1 <= len(body["key_points"]) <= 3

    # Every generated line must appear verbatim in the record's indexed text.
    haystack = SAMPLE_TEXT
    assert body["main_idea"] in haystack
    for point in body["key_points"]:
        assert point["text"] in haystack


def test_summary_never_mentions_absent_facts(summarisable, client):
    """A summary must not introduce a year, a name, or a citation of its own."""
    body = client.get(f"/api/v1/documents/{summarisable}/summary").json()

    rendered = f"{body['what_is_this']} {body['main_idea']} " + " ".join(
        p["text"] for p in body["key_points"]
    )
    # The record's own text contains no page numbers or citations; neither may the
    # summary, because it never saw a source it could legitimately point to.
    assert "[" not in rendered
    assert "p." not in rendered
    # "1916" is the record's year, so it is allowed in "what is this" only.
    assert "1916" not in body["main_idea"]


def test_summary_is_deterministic_and_needs_no_model(summarisable, client):
    first = client.get(f"/api/v1/documents/{summarisable}/summary").json()
    second = client.get(f"/api/v1/documents/{summarisable}/summary").json()
    assert first == second
    # Extractive by construction: nothing was generated, so no model is named.
    assert first["method"] == "extractive"
    assert first["model_id"] is None


def test_summary_declares_its_provenance(summarisable, client):
    body = client.get(f"/api/v1/documents/{summarisable}/summary").json()
    assert "AI-generated summary" in body["disclosure"]
    assert "archival record" in body["disclosure"]


def test_summary_honours_document_ids_filter_in_rag(seeded, client):
    """Asking a question scoped to one record must not pull in the other."""
    response = client.post(
        "/api/v1/rag/ask",
        json={
            "question": "What does this record say about education?",
            "document_ids": [],
            "max_evidence": 4,
            "min_evidence": 1,
            "persist": False,
        },
    )
    assert response.status_code == 200, response.text
    all_docs = {
        e["document_id"] for e in response.json().get("evidence", [])
    }

    scoped = client.post(
        "/api/v1/rag/ask",
        json={
            "question": "What does this record say about education?",
            "document_ids": ["women-and-education-1948"],
            "max_evidence": 4,
            "min_evidence": 1,
            "persist": False,
        },
    )
    assert scoped.status_code == 200, scoped.text
    scoped_docs = {e["document_id"] for e in scoped.json().get("evidence", [])}

    for document_id in scoped_docs:
        assert document_id in all_docs
    if scoped_docs:
        from app.db.base import session_scope
        from app.models.archive import Document

        with session_scope() as db:
            target = db.query(Document).filter(Document.slug == "women-and-education-1948").one()
        assert scoped_docs == {target.id}


def test_summary_of_unknown_document_is_404(seeded, client):
    assert client.get("/api/v1/documents/no-such-record/summary").status_code == 404


# --------------------------------------------------------------------------- #
# digitise
# --------------------------------------------------------------------------- #


def test_digitize_capabilities_reports_real_provider_state(client):
    body = client.get("/api/v1/digitize/capabilities").json()
    assert set(body["accepted_extensions"]) == {".jpg", ".jpeg", ".png", ".pdf"}
    assert body["max_upload_bytes"] > 0
    # Whatever the deployment has configured, the flag and the sentence must agree.
    if body["translation_available"] is False:
        assert "not configured" in body["translation_detail"].lower()
    if body["ocr_available"] is False:
        assert body["ocr_detail"]


def test_digitize_rejects_unsupported_type(client):
    response = client.post(
        "/api/v1/digitize",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert response.status_code == 422
    assert "Digitising accepts" in response.json()["detail"]


def test_digitize_rejects_empty_file(client):
    response = client.post("/api/v1/digitize", files={"file": ("blank.png", b"", "image/png")})
    assert response.status_code == 422


def test_digitize_rejects_a_file_over_its_own_limit(client, monkeypatch):
    """The digitise limit is its own, and an oversize file is stored nowhere.

    OCR runs inline in the request, so the admin 512 MB limit cannot apply here;
    the reported limit is the one the endpoint will actually enforce.
    """
    from app.api import digitize as digitize_module

    caps = client.get("/api/v1/digitize/capabilities").json()
    limit = caps["max_upload_bytes"]
    assert limit <= 32 * 1024 * 1024

    monkeypatch.setattr(digitize_module, "MAX_DIGITIZE_BYTES", 1024)
    response = client.post(
        "/api/v1/digitize",
        files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * 4096, "image/png")},
    )
    assert response.status_code == 413
    assert "Nothing was uploaded or stored" in response.json()["detail"]


def test_digitize_ocr_reads_the_upload_and_labels_it_unverified(seeded, client):
    """The result must be real OCR text, and must be marked as unverified."""
    caps = client.get("/api/v1/digitize/capabilities").json()
    if not caps["ocr_available"]:
        pytest.skip(f"OCR unavailable in this environment: {caps['ocr_detail']}")

    payload = _png_bytes()
    response = client.post(
        "/api/v1/digitize",
        files={"file": ("page-01.png", payload, "image/png")},
        data={"title": "Visitor manuscript", "source_language": "en"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["sha256"] == __import__("hashlib").sha256(payload).hexdigest()
    assert body["original_text"].strip(), "OCR produced no text for a readable scan"
    assert "ARCHIVE" in body["original_text"].upper()
    assert body["ocr_status"] in {"review", "approved"}
    assert body["is_draft"] is True, "a visitor upload must never be published"
    assert "original language" in body["disclosure"] or "OCR" in body["disclosure"]


def test_digitize_never_fakes_translation(seeded, client):
    """With no provider configured, English must be empty and the reason given."""
    caps = client.get("/api/v1/digitize/capabilities").json()
    if not caps["ocr_available"]:
        pytest.skip("OCR unavailable in this environment")

    response = client.post(
        "/api/v1/digitize",
        files={"file": ("page-02.png", _png_bytes(), "image/png")},
        data={"source_language": "en"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    if caps["translation_available"] is False:
        assert body["english_text"] is None
        assert body["translation_status"] == "unavailable"
        assert "not configured" in body["translation_detail"].lower()
        # The original-language OCR text is still returned, not blanked out.
        assert body["original_text"].strip()


def test_digitize_does_not_publish_or_disturb_the_archive(seeded, client):
    """An upload must not appear in public search or change published records."""
    before = client.get("/api/v1/documents", params={"page_size": 200}).json()["total"]

    caps = client.get("/api/v1/digitize/capabilities").json()
    if caps["ocr_available"]:
        client.post(
            "/api/v1/digitize",
            files={"file": ("page-03.png", _png_bytes(), "image/png")},
        )

    after = client.get("/api/v1/documents", params={"page_size": 200}).json()["total"]
    assert after == before, "a draft upload leaked into the public document list"


def test_digitize_preserves_the_original_bytes_unchanged(seeded, client):
    caps = client.get("/api/v1/digitize/capabilities").json()
    if not caps["ocr_available"]:
        pytest.skip("OCR unavailable in this environment")

    payload = _png_bytes("IMMUTABLE ORIGINAL")
    body = client.post(
        "/api/v1/digitize",
        files={"file": ("keepme.png", payload, "image/png")},
    ).json()

    from app.db.base import session_scope
    from app.models.archive import Document
    from app.providers.storage import get_object_store

    with session_scope() as db:
        document = db.get(Document, body["document_id"])
        key = document.storage_key
        assert document.checksum_sha256 == body["sha256"]
        assert document.publication_status != "published"

    stored = get_object_store().get(key)
    import hashlib

    assert hashlib.sha256(stored).hexdigest() == body["sha256"]
    assert stored == payload, "the preserved original was altered during processing"
