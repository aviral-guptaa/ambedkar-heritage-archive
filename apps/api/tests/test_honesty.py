"""The archive's honesty rules, expressed as tests.

These are not ordinary feature tests. Each one encodes a promise the interface
makes to a reader about what the archive knows, and each of them is a rule that
is easy to break accidentally while improving retrieval, formatting or
performance:

* a date is never invented to fill a gap;
* unpaginated text never claims a page;
* unverified text is never presented in quotation marks;
* a citation never claims verification its evidence does not have;
* a question the archive cannot support is refused rather than answered;
* unpublished records are not visible to the public.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("seeded")


# --------------------------------------------------------------- provenance --


def test_unpaginated_text_reports_no_page(client, seeded):
    """Continuous text must say the page is unknown, not cite page 1."""
    response = client.get("/api/v1/documents/caste-in-india-1916/text")
    assert response.status_code == 200
    payload = response.json()

    assert payload["parts"], "the record should have indexed text"
    for part in payload["parts"]:
        assert part["page_number"] is None
        assert part["page_information_unavailable"] is True


def test_public_document_exposes_verification_status(client, seeded):
    """A client must never have to guess whether text may be quoted."""
    listing = client.get("/api/v1/documents?limit=50").json()
    assert listing["items"], "the seeded records should be listed"

    for item in listing["items"]:
        assert item["verification_status"] == "unverified_secondary"

    detail = client.get("/api/v1/documents/caste-in-india-1916").json()
    assert detail["verification_status"] == "unverified_secondary"


def test_unverified_text_is_not_quoted(client, seeded):
    """The stored text endpoint must not dress unverified text as a quotation."""
    payload = client.get("/api/v1/documents/caste-in-india-1916/text").json()
    assert payload["quote_verified"] is False
    assert payload["provenance_warning"], "unverified text must carry a warning"
    assert "not be quoted" in payload["provenance_warning"].lower()


def test_search_results_declare_provenance(client, seeded):
    response = client.post("/api/v1/search", json={"query": "untouchability", "limit": 5})
    results = response.json()["results"]
    assert results

    for hit in results:
        assert hit["verification_status"] == "unverified_secondary"
        assert hit["quote_verified"] is False
        assert hit["page_number"] is None
        assert hit["page_information_unavailable"] is True


def test_year_only_record_keeps_year_precision(client, seeded):
    """A record known only to a year must not gain a month or a day."""
    detail = client.get("/api/v1/documents/women-and-education-1948").json()
    assert detail["year"] == 1948
    assert detail["document_date"] is None
    assert detail["date_precision"] == "year"


# ----------------------------------------------------------------- refusal --


def test_answer_from_unverified_text_is_labelled_not_grounded(client, seeded):
    response = client.post(
        "/api/v1/rag/ask",
        json={"question": "What did Ambedkar say about the abolition of untouchability?"},
    )
    assert response.status_code == 200
    answer = response.json()

    assert answer["answer_kind"] in {"unverified_summary", "grounded"}
    if answer["answer_kind"] == "unverified_summary":
        for mark in ('"', "“", "”"):
            assert mark not in answer["answer"], "unverified text must not be quoted"
        assert all(citation["verified"] is False for citation in answer["citations"])


@pytest.mark.parametrize(
    "question",
    [
        "What is the weather in Paris tomorrow?",
        "Which team won the 2019 cricket world cup?",
        "What is the population of Reykjavik?",
    ],
)
def test_unanswerable_question_is_refused(client, seeded, question):
    """The archive must decline rather than stretch an unrelated passage."""
    answer = client.post("/api/v1/rag/ask", json={"question": question}).json()

    assert answer["refused"] is True
    assert answer["answer_kind"] not in {"grounded", "unverified_summary"}
    assert answer["refusal_reason"], "a refusal must say why"
    assert answer["refusal_reason"] != answer["refusal_code"], (
        "the reason shown to a reader must be prose, not the internal token"
    )


def test_empty_question_is_refused(client, seeded):
    answer = client.post("/api/v1/rag/ask", json={"question": "   "}).json()
    assert answer["refused"] is True
    assert answer["refusal_code"] == "empty_question"


# ------------------------------------------------------------------ access --


def test_admin_routes_require_authentication(client, seeded):
    """Every admin route must refuse an anonymous caller.

    The routes are taken from the application rather than listed here, so a newly
    added admin endpoint is covered the moment it exists instead of quietly
    becoming the one endpoint that answers the public.
    """
    admin_routes = [
        (route.path, method)
        for route in client.app.routes
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        if getattr(route, "path", "").startswith("/api/v1/admin")
    ]
    assert admin_routes, "the archive should expose administrative routes"

    for path, method in admin_routes:
        response = client.request(method, path)
        assert response.status_code in {401, 403}, (
            f"{method} {path} answered an anonymous caller with {response.status_code}"
        )


def test_unpublished_record_is_not_public(client, db, seeded):
    """A draft must not be readable by the public, by slug or by identifier."""
    from app.db.base import session_scope
    from app.models.archive import Document
    from app.models.enums import PublicationStatus

    with session_scope() as db:
        document = Document(
            slug="draft-not-yet-published",
            title="An unpublished draft",
            document_type="speech",
            language="en",
            year=1950,
            verification_status="unverified_secondary",
        )
        db.add(document)
        db.flush()
        document_id = document.id
        draft_slug = document.slug

    try:
        assert client.get(f"/api/v1/documents/{draft_slug}").status_code == 404
        assert client.get(f"/api/v1/documents/{draft_slug}/text").status_code == 404
        assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
        listed = client.get("/api/v1/documents?limit=50").json()
        assert draft_slug not in {item["slug"] for item in listed["items"]}
    finally:
        from app.db.base import session_scope as scope

        with scope() as db:
            db.query(Document).filter(Document.id == document_id).delete()
            db.query(Document).filter(Document.publication_status == PublicationStatus.DRAFT).filter(
                Document.slug == "draft-not-yet-published"
            ).delete()
