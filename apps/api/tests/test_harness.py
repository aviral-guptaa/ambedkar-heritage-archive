"""A first pass over the harness itself, so a failure elsewhere is unambiguous."""


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}


def test_migrations_reach_head(migrated_database):
    assert migrated_database


def test_seeded_archive_is_searchable(client, seeded):
    response = client.post("/api/v1/search", json={"query": "untouchability", "limit": 5})
    assert response.status_code == 200
    assert response.json()["total"] >= 1
