"""Serving the built interface from the API process.

A kiosk has one port, and a hosted deployment should too. Running nginx in front
of uvicorn is the better arrangement when you control the machine — it terminates
TLS, serves static files without waking Python, and keeps the two concerns
separate. But a platform that exposes a single port per service (Render, for
example) cannot do that without a second process to supervise, and a supervisor
that can die quietly is worse than no supervisor.

So this module makes the API able to serve the built interface itself, and only
when it is told to. The nginx arrangement stays the default for a kiosk; this
exists so a one-service deployment is possible without changing the code.

Two rules are enforced here rather than left to configuration:

* the API prefix always wins, so a route can never be shadowed by a file;
* ``sw.js`` and ``offline.html`` are served with caching turned off, because a
  browser holding an old service worker would serve a withdrawn record
  indefinitely — the same rule the nginx config states, for the same reason.

**Call this after the API routes are registered.** Starlette matches routes in
the order they were added, so a catch-all added first will happily answer
``/api/v1/health`` with a page of HTML, and every endpoint in the application
will return 404 with no other symptom. ``create_app`` includes the routers first
for that reason; ``test_spa.py`` pins the ordering, and so does
``test_openapi_schema.py`` against the real application.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

#: Files that must never be cached by the browser. The service worker decides
#: what is available offline; if the browser cached the worker itself, a new
#: build could not take effect.
NO_STORE = {"/sw.js", "/offline.html", "/index.html", "/manifest.webmanifest"}


class CachedStaticFiles(StaticFiles):
    """Static files with an explicit caching policy.

    Starlette's ``StaticFiles`` sends ``ETag`` and ``Last-Modified`` but no
    ``Cache-Control``, so a browser revalidates every asset on every visit. That
    is safe and it is also a round trip per file, per page load, forever.

    Vite writes hashed filenames into ``assets/``, which is exactly the case
    where a long cache is safe: a changed file has a changed name, so a cached
    copy can never be stale.
    """

    def __init__(self, *args: object, cache_control: str, **kwargs: object) -> None:
        self.cache_control = cache_control
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def file_response(self, *args: object, **kwargs: object) -> Response:
        response = super().file_response(*args, **kwargs)  # type: ignore[arg-type]
        response.headers["Cache-Control"] = self.cache_control
        return response


def mount_interface(app: FastAPI, dist_dir: Path, api_prefix: str) -> bool:
    """Serve the built interface from ``dist_dir``.

    Returns ``True`` when the interface was mounted. A missing directory is not
    an error: in development the interface is served by Vite, and the API is
    expected to run without it.
    """
    index = dist_dir / "index.html"
    if not index.is_file():
        return False

    assets = dist_dir / "assets"
    if assets.is_dir():
        # Hashed filenames, so they can be cached hard.
        app.mount(
            "/assets",
            CachedStaticFiles(directory=assets, cache_control="public, max-age=31536000, immutable"),
            name="assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        # No `request: Request` parameter. It is not needed here, and under
        # `from __future__ import annotations` FastAPI cannot resolve that
        # annotation in a closure, which takes /openapi.json down with it.
        path = "/" + full_path

        # The API always wins. Without this a catch-all would shadow a route
        # added later, and the failure would look like a missing endpoint rather
        # than a routing mistake.
        if path == api_prefix or path.startswith(api_prefix + "/"):
            return PlainTextResponse("Not Found", status_code=404)

        if path in ("/docs", "/redoc", "/openapi.json"):
            return PlainTextResponse("Not Found", status_code=404)

        # Resolve inside dist and refuse anything that escapes it. A request for
        # `../../etc/passwd` is a normal thing to receive on a public server.
        if full_path:
            candidate = (dist_dir / full_path).resolve()
            try:
                candidate.relative_to(dist_dir.resolve())
            except ValueError:
                return PlainTextResponse("Not Found", status_code=404)
            if candidate.is_file():
                return FileResponse(
                    candidate,
                    headers=(
                        {"Cache-Control": "no-cache, must-revalidate"}
                        if path in NO_STORE
                        else {"Cache-Control": "public, max-age=3600"}
                    ),
                )

            # A request that names a file and did not find one is not a
            # client-side route. Answering it with the shell would turn a missing
            # asset, or an API base with a typo in it, into a 200 full of HTML —
            # a miserable thing to debug, because the caller has to notice the
            # content type to understand the answer. nginx makes the same
            # distinction, which is why /sw.js still works and /missing.js does
            # not get a page.
            if "." in full_path.rsplit("/", 1)[-1]:
                return PlainTextResponse("Not Found", status_code=404)

        # Anything else is a client-side route, so the shell answers and the
        # router takes it from here.
        return FileResponse(
            index,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    return True
