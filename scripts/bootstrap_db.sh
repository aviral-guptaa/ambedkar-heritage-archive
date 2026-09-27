#!/usr/bin/env bash
# Provision the local PostgreSQL database for the Digital Heritage Archive.
#
# Run once as a PostgreSQL superuser (the Homebrew `postgres` role on macOS):
#     ./scripts/bootstrap_db.sh
#
# It is idempotent. The application role deliberately stays unprivileged: the
# pgvector and pg_trgm extensions are created here, and the Alembic migrations
# then find them already present, so migrations run as the unprivileged role.
set -euo pipefail

APP_DB="${APP_DB:-dha}"
APP_USER="${APP_USER:-dha}"
APP_PASSWORD="${APP_PASSWORD:-dha_dev_password}"
PSQL_BIN="${PSQL_BIN:-$(command -v psql || echo /opt/homebrew/opt/postgresql@16/bin/psql)}"

if ! command -v "$PSQL_BIN" >/dev/null 2>&1 && [ ! -x "$PSQL_BIN" ]; then
  echo "psql not found. Set PSQL_BIN or install PostgreSQL 16 (brew install postgresql@16)." >&2
  exit 1
fi

echo "==> ensuring PostgreSQL is running"
if ! pg_isready -q 2>/dev/null; then
  if command -v brew >/dev/null 2>&1; then
    brew services start postgresql@16 || true
    sleep 3
  fi
fi
pg_isready || { echo "PostgreSQL is not accepting connections." >&2; exit 1; }

echo "==> ensuring pgvector is installed"
EXT_COUNT="$("$PSQL_BIN" -d postgres -tAc "SELECT count(*) FROM pg_available_extensions WHERE name='vector'")"
if [ "$EXT_COUNT" = "0" ]; then
  cat <<'MSG'
pgvector is not installed for this PostgreSQL version.

  macOS (bottles ship PG17/18, so build against your version):
      curl -sSL https://github.com/pgvector/pgvector/archive/refs/tags/v0.8.6.tar.gz \
        | tar xz && cd pgvector-0.8.6 \
        && make PG_CONFIG="$(pg_config --bindir)/pg_config" && sudo make PG_CONFIG="$(pg_config --bindir)/pg_config" install

  Debian/Ubuntu:
      apt install postgresql-16-pgvector
MSG
  exit 1
fi

echo "==> creating role ${APP_USER}"
"$PSQL_BIN" -d postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${APP_USER}') THEN
    CREATE ROLE ${APP_USER} LOGIN PASSWORD '${APP_PASSWORD}';
  ELSE
    ALTER ROLE ${APP_USER} LOGIN PASSWORD '${APP_PASSWORD}';
  END IF;
END
\$\$;
SQL

echo "==> creating database ${APP_DB}"
if [ "$("$PSQL_BIN" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${APP_DB}'")" != "1" ]; then
  "$PSQL_BIN" -d postgres -c "CREATE DATABASE ${APP_DB} OWNER ${APP_USER}"
fi

echo "==> enabling extensions"
"$PSQL_BIN" -d "${APP_DB}" -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION IF NOT EXISTS vector" \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"

echo "==> verifying vector similarity"
"$PSQL_BIN" -d "${APP_DB}" -tAc "SELECT 'pgvector ' || extversion FROM pg_extension WHERE extname='vector'"

echo "Done. Next: cd apps/api && python -m alembic upgrade head"
