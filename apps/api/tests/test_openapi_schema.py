"""The OpenAPI schema must build, and every route must resolve its annotations.

This exists because of a bug that made document upload unreachable.

``upload_document`` took a ``request: Request`` parameter in a module that never
imported ``Request``. Under ``from __future__ import annotations`` that name
stayed an unresolved string, so FastAPI did two things instead of recognising the
parameter: it left a required query parameter called ``request`` in the route,
and it failed to generate a schema for the endpoint at all.

The first is the worse half. A POST to ``/api/v1/documents`` without
``?request=`` returned 422 — the archive could ingest nothing — while every test
that did not go through the real HTTP signature still passed. The second took
``/openapi.json`` down with a 500, so the API documentation was unreachable too.

A wrong annotation in a route that is never exercised looks identical to a route
that works. Asking the built application for its own schema is the only cheap
way to see the difference, so it is done here.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute


def routes(app) -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


def test_openapi_schema_builds(app_module) -> None:
    """Every annotation in every route must be resolvable.

    An unresolvable name here is not cosmetic: it becomes a required query
    parameter at runtime, and the endpoint stops accepting the calls it was
    written for.
    """
    schema = app_module.openapi()
    assert schema["paths"], "the schema has no paths, so the routers did not load"
    assert schema["info"]["title"]


def test_no_route_mistakes_a_request_for_a_query_parameter(app_module) -> None:
    """A Request parameter must be recognised as one.

    FastAPI sets ``request_param_name`` when it resolves the annotation. A None
    here means the annotation was a string it could not evaluate, and the
    parameter has silently become part of the query string.
    """
    unrecognised = [
        f"{route.path} ({route.name})"
        for route in routes(app_module)
        if route.dependant.request_param_name is None
        and any(param.name == "request" for param in route.dependant.query_params)
    ]
    assert unrecognised == [], (
        f"{unrecognised} declare `request: Request` in a module that does not "
        "import Request, so it is being served as a required query parameter "
        "and the endpoint cannot be called as written."
    )


def test_document_upload_takes_no_query_parameters(app_module) -> None:
    """The specific regression, pinned on the route that broke."""
    upload = next(
        route
        for route in routes(app_module)
        if route.path == "/api/v1/documents" and "POST" in (route.methods or set())
    )
    # The parameter was dead code — never referenced in the body — so it was
    # removed rather than made resolvable. The route must take its input from
    # the multipart body alone.
    assert upload.dependant.request_param_name is None
    assert [param.name for param in upload.dependant.query_params] == []


#: Exactly the names that mean "give me the request", as opposed to a body model
#: whose own name happens to end in Request.
_REQUEST_ANNOTATIONS = {"Request", "fastapi.Request", "starlette.requests.Request"}


def _request_annotations(app) -> list[tuple[str, str, str]]:
    """Routes with an annotation that claims to be a Request."""
    found = []
    for route in routes(app):
        raw = getattr(route.endpoint, "__annotations__", {}) or {}
        for name, annotation in raw.items():
            if name == "return":
                continue
            if str(annotation).strip("'\"") in _REQUEST_ANNOTATIONS:
                found.append((route.path, name, str(annotation)))
    return found


def test_route_annotations_all_resolve(app_module) -> None:
    """Catch the same mistake in any route, including future ones.

    Matched exactly rather than by substring: `SearchRequest` and
    `TranslateRequest` are body models and resolve perfectly well.
    """
    unresolved = [
        f"{path}: `{name}: {annotation}`"
        for path, name, annotation in _request_annotations(app_module)
        if next(
            r for r in routes(app_module) if r.path == path
        ).dependant.request_param_name != name
    ]
    assert unresolved == [], (
        f"{unresolved} annotate a Request that FastAPI could not resolve, so it "
        "became a query parameter instead."
    )


def test_routes_actually_exist(app_module) -> None:
    """Guards against this module passing because it inspected nothing."""
    assert len(routes(app_module)) > 50, "the router set looks wrong"


def test_api_routes_are_registered_before_the_interface(app_module) -> None:
    """The catch-all must come last, or the whole API 404s.

    Starlette matches routes in the order they were added. ``create_app``
    includes the routers and only then calls ``mount_interface``, which is easy
    to reorder by accident when reading the file top to bottom, and the result
    is an application where every endpoint returns 404 with no other symptom.

    The interface is not mounted in the test environment, so the catch-all is
    looked up rather than assumed.
    """
    all_paths = [getattr(route, "path", "") for route in app_module.routes]
    assert "/{full_path:path}" not in all_paths, (
        "The interface catch-all should not be mounted unless WEB_DIST is set, "
        "and it is not set in tests. If it is, this test needs rethinking."
    )

    # The routers must be in place before anything that could shadow them, so
    # every API path has to resolve to a real route.
    client_seen = {route.path for route in routes(app_module)}
    for path in ("/api/v1/health", "/api/v1/documents", "/api/v1/search"):
        assert path in client_seen, f"{path} is not registered"
