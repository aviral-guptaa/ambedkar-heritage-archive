"""The single-port interface must not become a hole in the API.

When FastAPI serves the built interface, one catch-all route sits in front of
everything. That is convenient and it is also the easiest place in this project
to quietly break the API, because a catch-all matches anything.

The rules it has to keep:

* the API prefix is never shadowed, so a route added later cannot disappear
  behind a file;
* requests cannot escape the build directory;
* a request that names a file and finds nothing is a 404, not a page of HTML —
  otherwise a mistyped API base comes back as ``200 text/html`` and the caller
  has to notice the content type to work out what went wrong;
* ``sw.js`` and ``offline.html`` are served, but never cached, or a browser
  holds a withdrawn record indefinitely.

These run against a real temporary build directory rather than a mock, because
the interesting cases are all about what is actually on disk.

One thing worth knowing before reading the traversal tests: an HTTP client
normalises ``/../../etc/passwd`` to ``/etc/passwd`` before the application sees
it, so those requests never reach the handler. The handler's own containment is
therefore tested by calling it directly, which is the only way to exercise it.
"""

from __future__ import annotations

import anyio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.spa import mount_interface

API_PREFIX = "/api/v1"


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    """A build directory shaped like the one Vite produces."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>archive</title>")
    (tmp_path / "sw.js").write_text("// service worker")
    (tmp_path / "offline.html").write_text("<!doctype html><title>offline</title>")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    return tmp_path


@pytest.fixture()
def app(dist: Path) -> FastAPI:
    application = FastAPI()

    # The API route first, the catch-all second. This order is the whole reason
    # `create_app` includes its routers before calling `mount_interface`, and
    # reversing it here is what made this fixture fail with a 404 on a route
    # that plainly exists.
    @application.get(f"{API_PREFIX}/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    assert mount_interface(application, dist, API_PREFIX) is True
    return application


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def call_catch_all(app: FastAPI, full_path: str):
    """Invoke the catch-all directly, bypassing client-side normalisation."""
    route = next(
        r for r in app.routes if getattr(r, "path", "") == "/{full_path:path}"
    )
    return anyio.run(route.endpoint, full_path)


def test_the_catch_all_must_be_added_after_the_api_routes(dist: Path) -> None:
    """Documents the ordering trap that `mount_interface` warns about.

    Added first, the catch-all answers every API path with a 404 and the only
    symptom is an application where nothing works.
    """
    application = FastAPI()
    mount_interface(application, dist, API_PREFIX)

    @application.get(f"{API_PREFIX}/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    assert TestClient(application).get("/api/v1/health").status_code == 404


def test_a_missing_build_directory_is_not_an_error(tmp_path: Path) -> None:
    """In development the interface is served by Vite, and the API must still run."""
    assert mount_interface(FastAPI(), tmp_path / "not-built", API_PREFIX) is False


def test_a_directory_without_an_index_is_not_an_interface(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    assert mount_interface(FastAPI(), tmp_path, API_PREFIX) is False


def test_the_shell_is_served_at_the_root(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "archive" in response.text


@pytest.mark.parametrize(
    "route",
    ["/manuscripts", "/search", "/timeline", "/source-verification", "/a/deep/route"],
)
def test_client_routes_get_the_shell(client: TestClient, route: str) -> None:
    response = client.get(route)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_real_files_are_served(client: TestClient) -> None:
    assert client.get("/sw.js").status_code == 200
    assert client.get("/offline.html").status_code == 200
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/assets/index-abc123.js").status_code == 200


def test_the_api_is_never_shadowed_by_the_catch_all(client: TestClient) -> None:
    """A route added later must not disappear behind the interface."""
    assert client.get("/api/v1/health").json() == {"status": "ok"}

    # An unknown API path is a 404, not the shell. Without this, adding a route
    # with a typo in its prefix would look like it had been implemented.
    unknown = client.get("/api/v1/does-not-exist")
    assert unknown.status_code == 404
    assert not unknown.headers["content-type"].startswith("text/html")


def test_a_missing_file_is_a_404_not_a_page(client: TestClient) -> None:
    """A mistyped asset or API base must not come back as 200 HTML.

    ``/health`` is deliberately absent from this list: it has no file extension,
    so from the catch-all's point of view it is an ordinary client route. What
    makes a missing API call visible is that it has an extension, or the API
    prefix.
    """
    for path in ("/missing.js", "/assets/gone.css", "/api/v1", "/manifest.webmanifest.bak"):
        response = client.get(path)
        assert response.status_code == 404, f"{path} returned {response.status_code}"
        assert not response.headers["content-type"].startswith("text/html")


def test_a_404_says_what_it_is(client: TestClient) -> None:
    """A bare Response with no media type sends no Content-Type at all."""
    response = client.get("/missing.js")
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "Not Found"


@pytest.mark.parametrize(
    "path",
    [
        "/../../etc/passwd",
        "/assets/../../../../etc/passwd",
        "/....//....//etc/passwd",
        "/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/etc/passwd",
    ],
)
def test_traversal_over_http_never_returns_a_file(client: TestClient, path: str) -> None:
    """The client normalises these before the application sees them.

    What matters is that none of them ever returns file contents. A 200 here
    means the SPA shell, which is what any unknown extensionless route gets.
    """
    response = client.get(path)
    assert "root:" not in response.text
    assert "archive" in response.text or response.status_code == 404


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "..",
        "/etc/passwd",
        "assets/../../../../etc/passwd",
        "sw.js/../../../../etc/passwd",
    ],
)
def test_the_handler_refuses_to_escape_the_build_directory(
    app: FastAPI, hostile: str
) -> None:
    """Tested directly, because no HTTP client will send these intact.

    ``....//`` is not a traversal — ``....`` is a valid directory name — so it is
    absent here; it correctly falls through to the shell.
    """
    response = call_catch_all(app, hostile)
    assert response.status_code == 404, f"{hostile!r} was not refused"
    assert "root:" not in response.body.decode()


@pytest.mark.parametrize("path", ["docs", "redoc", "openapi.json"])
def test_documentation_paths_are_defensively_refused(app: FastAPI, path: str) -> None:
    """FastAPI registers these itself, so the catch-all never sees them.

    The branch is kept anyway: if the docs URL were ever disabled, ``/docs`` has
    no file extension and would otherwise be answered with the shell.
    """
    assert call_catch_all(app, path).status_code == 404


def test_fastapi_still_serves_its_own_documentation(client: TestClient) -> None:
    """The catch-all must not interfere with what FastAPI registers itself."""
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_the_shell_and_the_worker_are_never_cached(client: TestClient) -> None:
    """A cached service worker would keep serving a withdrawn record."""
    for path in ("/", "/index.html", "/sw.js", "/offline.html", "/manifest.webmanifest"):
        assert "no-cache" in client.get(path).headers["cache-control"], path


def test_hashed_assets_are_cacheable(client: TestClient) -> None:
    """Otherwise every page load revalidates every asset, forever.

    Starlette's StaticFiles sets no Cache-Control at all, so this is a subclass
    rather than a header in the catch-all: assets are served by the mount, which
    matches before the catch-all and therefore never reaches it.
    """
    cache_control = client.get("/assets/index-abc123.js").headers["cache-control"]
    assert "max-age=31536000" in cache_control
    assert "immutable" in cache_control
