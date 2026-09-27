# The archive API and the archivist-facing web build.
#
# Written for a kiosk that must run with no internet: every layer below is
# pinned, and the model weights the retriever needs are baked in rather than
# downloaded on first run. A kiosk that cannot reach a package index is a kiosk
# that cannot start.

# ---------------------------------------------------------------- frontend --
FROM node:22-bookworm-slim AS web
WORKDIR /build

COPY apps/web/package.json apps/web/package-lock.json* ./
# `npm ci` when a lockfile exists, so a kiosk build is reproducible; the
# fallbacks keep this working before one has been committed.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY apps/web/ ./
RUN npm run build

# ------------------------------------------------------------------- web --
# Serves the built interface and proxies the API, so a kiosk needs one port
# rather than two and no CORS configuration.
FROM nginx:1.27-alpine AS serve
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web /build/dist /usr/share/nginx/html
# The service worker and the shell must never be cached by the browser, or a
# kiosk can serve a withdrawn record indefinitely.
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=4s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

# ------------------------------------------------------------------- api --
FROM python:3.12-slim-bookworm AS api

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Refuse to run as root. A kiosk displaying a third-party corpus should not
    # hand that corpus a process that can write to the system.
    ENVIRONMENT=production \
    DEBUG=false

# tesseract is the OCR engine; the rest are what psycopg and the wheel-free
# wheels for cryptography need at build time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-hin \
        tesseract-ocr-mar \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/archive

COPY apps/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api/alembic.ini ./
COPY apps/api/alembic/ ./alembic/
COPY apps/api/app/ ./app/
COPY apps/api/scripts/ ./scripts/
COPY apps/api/data/ ./data/

# Run as an unprivileged user. The directories the archive writes to — the
# object store, the logs, and anything a curator uploads — are created here so
# they are writable without granting write access to the application code.
RUN useradd --create-home --uid 10001 archive \
    && mkdir -p /var/lib/dha/objects /var/lib/dha/var /var/log/dha \
    && chown -R archive:archive /srv/archive /var/lib/dha /var/log/dha
USER archive

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

# The migrations run before the server starts, so a kiosk that has just been
# powered on does not serve a database whose schema is one version behind.
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
