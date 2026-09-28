# Operations

## What is verified, and what is not

Read this before trusting any instruction in this file.

| Checked on this machine | Not checked |
| --- | --- |
| 110 backend tests, 54 frontend tests, 21 kiosk tests | `docker compose up` — Docker was not installed |
| Frontend production build, TypeScript strict typecheck | Verified source promotion — archival sites return Cloudflare challenges |
| 31 contract, 33 admin, 16 offline, 13 i18n and 108 route checks against a running instance | Electron packaging (`electron-builder`) |
| A real worker completing a `FINALISE` job end to end | |
| An inline-queue `FINALISE` job completing in-process, with no worker | |
| Electron launching against the real archive, in both normal and kiosk mode | |
| The service worker's offline behaviour in Chrome | |

Anything not in the left-hand column has not been run. If a step below depends
on one of those, it is marked.

## Requirements

- Python 3.12 (3.11+ works)
- Node 22
- PostgreSQL 16 with `pgvector`
- Redis, optional — the database queue backend works without it
- Tesseract with `eng`, `hin` and `mar`, for OCR

## First run

```bash
git clone <repository>
cd ambedkar-heritage-archive

python3 -m venv .venv
.venv/bin/pip install -r apps/api/requirements.txt

# The schema needs the vector extension before the migration runs.
psql -c 'CREATE EXTENSION IF NOT EXISTS vector;' -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'

export DATABASE_URL=postgresql+psycopg://localhost/archive
export STORAGE_ROOT="$PWD/var"
cd apps/api && ../../.venv/bin/python -m alembic upgrade head && cd ../..
```

Note the path: from `apps/api` the virtualenv is at `../../.venv`, not
`../.venv`.

Create the first user. There is no default password anywhere in this repository:

```bash
.venv/bin/python apps/api/scripts/create_user.py \
  --email you@example.org --password 'choose-something' --role SUPER_ADMIN
```

Import the corpus:

```bash
.venv/bin/python apps/api/scripts/import_corpus.py
```

The importer is idempotent. Re-running it will not duplicate records, and it
refuses to proceed if the dataset's checksum has changed, because that would
mean the source data had been altered:

```bash
sha256sum apps/api/data/speeches.jsonl
# b9806ef55e32ecc1577eed18c85c305778add6c73832b425ea77e6b9e87b9e7b
```

## Running it

```bash
# API
.venv/bin/python -m uvicorn app.main:app --app-dir apps/api --port 8099

# worker — required; without it jobs queue and never run
.venv/bin/python -m app.worker        # from apps/api, with the venv on PATH

# interface
cd apps/web && npm install && npm run dev
```

There is a detached API launcher at `scripts/dev_api.py`.

**The worker is not optional.** `/api/v1/health` reports
`status: "degraded"` and `workers_alive: 0` until one is running, and ingestion
will not happen without it.

## Tests

```bash
# Backend: needs PostgreSQL. Uses a disposable migrated database per test.
.venv/bin/python -m pytest apps/api/tests -q

# Frontend
cd apps/web && npm run test && npx tsc --noEmit && npm run build

# Browser checks: the API on 8099 and `npm run preview` on 4173
cd apps/web
npm run contract       # 31 checks against a running API
npm run check:routes   # every public route, in all three languages
npm run check:i18n     # the language control and its persistence
npm run check:offline  # the service worker, with the browser taken offline

# The admin check needs a real account, supplied by the environment. There is
# no default credential in the repository, on purpose.
cd apps/api && ../../.venv/bin/python scripts/create_user.py \
  --email admin-check@example.org --password "$PW" --role ARCHIVIST && cd ../web
DHA_ADMIN_EMAIL=admin-check@example.org DHA_ADMIN_PASSWORD="$PW" npm run check:admin
```

The browser checks need a Chromium binary. Set `CHROME_PATH` if it is not in the
default location. All of them default to the preview server on 4173; override
with `WEB_URL`.

The admin check will wait out the sign-in rate limit (15 a minute) rather than
failing, because the limit is a real control and should not be worked around.

## Deployment

```bash
cp .env.example .env    # then set POSTGRES_PASSWORD at minimum
docker compose up -d --build
```

**This has never been run.** Docker was not available on the build machine. The
files are written to be read and checked, not trusted. Before relying on them:

1. `docker compose config` — validates the file.
2. `docker compose build` — the frontend stage and the wheel build.
3. `docker compose up -d` — then `curl localhost:8080/api/v1/health`.

For a venue with no network at all, `Dockerfile.kiosk` builds a single image
containing the API, the interface, and PostgreSQL:

```bash
docker build -f Dockerfile.kiosk -t ambedkar-kiosk .
docker run -d --name kiosk -p 8000:8000 ambedkar-kiosk
```

Expect to be online at least once to fetch the base images and dependencies.
After that the image is self-contained.

## The kiosk

```bash
cd apps/kiosk && npm install
HW_KIOSK_MODE=true HW_SPEAKER_ENABLED=true npm start
```

In kiosk mode the interface is fullscreen and the mouse is hidden. For
development, leave `HW_KIOSK_MODE` unset.

Configuration is documented in [ARCHITECTURE.md](ARCHITECTURE.md#the-kiosks-hardware-profile).

If `npm install` does not fetch the Electron binary — some environments block
install scripts — install it directly:

```bash
npx --yes @electron/get@2.0.3
# then unpack the downloaded zip over node_modules/electron/dist
```

## Verifying a source

This is the only way a record becomes `verified_primary`, and it is currently
blocked. `columbia.edu` and the Government of India both return Cloudflare
challenges, so the fetch fails and the script exits non-zero. That is correct
behaviour, not a bug to work around.

List what still needs checking:

```bash
cd apps/api
../../.venv/bin/python scripts/verify_source.py --list
```

Compare one document against its own cited source, without changing anything:

```bash
../../.venv/bin/python scripts/verify_source.py --slug SPEECH-BRA-1948-02
```

This fetches `document.source_url` and reports whole-document similarity, which
sampled passages matched verbatim, and the SHA-256 of what was fetched. It exits
non-zero if the comparison fails.

Promote it, if the comparison passed and a named person stands behind the
decision:

```bash
../../.venv/bin/python scripts/verify_source.py \
  --slug SPEECH-BRA-1948-02 \
  --promote \
  --reviewer you@example.org
```

`--promote` without `--reviewer` is refused. Promotion also requires the
comparison to pass at `--character-threshold` (default 0.97) and
`--sample-threshold` (default 1.0 — every sampled passage must match verbatim).
The thresholds are arguments, so someone could pass a low one; the audit log
records the reviewer and the thresholds actually used, and a low threshold is
visible in the record.

The script does not offer a force flag, and it should not grow one: the ability
to mark a source as verified without having read it is the single thing this
project exists to prevent.

If a source is only reachable from a browser — behind a challenge, or in a
paywalled archive — a human has to do this comparison by hand, and the
provenance record should say so.

## Backups

Back up the database and the object store. The cache is not backup-worthy; it
can be rebuilt from the network.

```bash
pg_dump "$DATABASE_URL" -Fc -f archive-$(date +%F).dump
tar czf objects-$(date +%F).tar.gz "$STORAGE_ROOT/objects"
apps/api/data/speeches.jsonl     # the imported corpus, in version control
```

Test a restore. An untested backup is a hypothesis.

## When something is wrong

**Health says `degraded`.** Read `degraded_modes` and `workers_alive` in the
response rather than guessing. A missing worker is the usual cause.

**The kiosk loads but the page is blank.** Open the developer console with
`HW_KIOSK_MODE=false` and start from there. A blank page with a working
network is nearly always a service worker holding a stale shell; in development
the worker is not registered, so this points at the API instead.

**Search returns nothing.** Check `degraded_modes` for `vector_search`. The
lexical fallback does not rank the same way, and with an empty index it returns
nothing at all — which is the honest answer.

**A job is stuck.** The job monitor in the admin interface shows the queue and
its failures. `FINALISE` runs at the end of ingestion; if it fails, ingestion
has not completed and the document will not be searchable.

**A record will not import.** The importer validates the dataset checksum and
refuses to proceed if it has changed. That is intentional. Investigate the
change rather than disabling the check.

## Things that will look like bugs

- **`Page information unavailable in indexed source.`** Correct. The source is
  unpaginated.
- **No quotation marks anywhere.** Correct. See [HONESTY.md](HONESTY.md).
- **An empty graph.** Relationships are extracted during ingestion, which needs
  a worker.
- **Answers refusing a question.** The archive refuses questions it cannot
  answer from the corpus. It says why, and the reason is not hidden.
- **The offline banner on a machine with a network.** A stored copy is being
  shown; the banner is doing its job.
