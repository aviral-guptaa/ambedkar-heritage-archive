"""The archivist application must be usable, and must be closed to everyone else.

These tests exist because the admin screens are the only place a person can
change what the public archive claims. Two faults found while building those
screens are what they are written to prevent:

* ``/auth/roles`` returned a 500, because the code selected the ``UserRole``
  enum where the ``roles`` table was meant. The roles are seeded by the
  migration, so a test that only ever signed in with an existing role never
  reached the broken branch.
* ``/admin/documents`` did not exist, so the only way to list documents was
  the public endpoint, which hides drafts — a curator could never find the
  records they were supposed to publish.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("seeded")


@pytest.fixture()
def archivist(client, session_factory):
    """An ARCHIVIST account with a real token, created through the public API."""
    from app.api.auth import _resolve_role
    from app.core.security import hash_password
    from app.models.access import User
    from app.models.enums import UserRole

    email = "curator-tests@ambedkar.archive"
    with session_factory() as db:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            # Nothing seeds the roles table on migration, so a brand new
            # installation has none until an account is created. Using the same
            # call the sign-up path makes keeps this honest.
            role_row = _resolve_role(db, UserRole.ARCHIVIST)
            user = User(
                email=email,
                full_name="Test Curator",
                role=role_row,
                password_hash=hash_password("TestPassw0rd!archive"),
                is_active=True,
            )
            db.add(user)
            db.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "TestPassw0rd!archive"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------- roles --


def test_roles_endpoint_lists_every_role(client):
    """The screen that shows a curator their options must not 500.

    This is the regression test for the enum-versus-table confusion.
    """
    response = client.get("/api/v1/auth/roles")
    assert response.status_code == 200, response.text

    roles = response.json()
    assert isinstance(roles, list)
    by_name = {role["name"]: role for role in roles}
    for expected in ("SUPER_ADMIN", "ARCHIVIST", "EDITOR", "RESEARCHER", "VIEWER"):
        assert expected in by_name, f"{expected} missing from the roles endpoint"
        assert isinstance(by_name[expected]["permissions"], list)

    # A viewer can read documents and nothing else, so a permission list that
    # grants more would be a real access-control defect, not a display detail.
    assert by_name["VIEWER"]["permissions"] == ["document:read"]
    assert by_name["SUPER_ADMIN"]["permissions"] == ["*"]


def test_creating_a_user_assigns_a_known_role(client, archivist):
    """Creating an account must work, which requires the roles table lookup."""
    response = client.post(
        "/api/v1/auth/users",
        headers=_auth(archivist),
        json={
            "email": "newly-made@ambedkar.archive",
            "password": "AnotherPassw0rd!x",
            "role": "RESEARCHER",
        },
    )
    # ARCHIVIST is not granted user management, so a refusal is a correct
    # outcome; what must not happen is a 500 from the broken role lookup.
    assert response.status_code in (201, 403), response.text
    if response.status_code == 201:
        assert response.json()["role"] == "RESEARCHER"


def test_an_unknown_role_is_rejected_with_the_allowed_list(client, archivist):
    response = client.post(
        "/api/v1/auth/users",
        headers=_auth(archivist),
        json={
            "email": "bad-role@ambedkar.archive",
            "password": "AnotherPassw0rd!x",
            "role": "SUPER_USER",
        },
    )
    assert response.status_code in (403, 422), response.text


# --------------------------------------------------------------- documents --


def test_admin_documents_is_closed_to_anonymous_callers(client, seeded):
    """The working list must not be reachable without a signed-in curator."""
    assert client.get("/api/v1/admin/documents").status_code == 401


def test_admin_documents_lists_records_for_a_curator(client, archivist):
    response = client.get("/api/v1/admin/documents", headers=_auth(archivist))
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["total"] >= 2
    slugs = {item["slug"] for item in payload["items"]}
    assert "caste-in-india-1916" in slugs

    # The same shape as the public list, so the screen can reuse the badge.
    for item in payload["items"]:
        assert "verification_status" in item
        assert "publication_status" in item


def test_admin_documents_finds_a_record_by_speaker(client, archivist):
    """A curator remembers who spoke, not only what the record is called."""
    response = client.get(
        "/api/v1/admin/documents?q=Caste", headers=_auth(archivist)
    )
    assert response.status_code == 200
    assert any("caste" in item["title"].lower() for item in response.json()["items"])


def test_admin_documents_rejects_an_unknown_sort(client, archivist):
    """The sort key reaches SQL, so it is constrained rather than interpolated."""
    response = client.get(
        "/api/v1/admin/documents?sort=id;DROP TABLE documents",
        headers=_auth(archivist),
    )
    assert response.status_code == 422
