# Dr. B. R. Ambedkar Digital Heritage Archive

A searchable, provenance-tracking archive of Dr. B. R. Ambedkar's speeches, built
for SIH 2026 problem [`SIH26096`](https://sih.decodex.live/sih2026/SIH26096)
(category: Hardware; theme: Smart Education).

The archive is built around one principle: **a reader must always be able to tell
what the archive actually knows from what it merely contains.** Every record
carries its provenance, every citation carries the verification status of the text
it points at, and the system refuses to answer rather than sound plausible.

**Read [docs/HONESTY.md](docs/HONESTY.md) before using this.** It is the
constraint the rest of the project is built around.

---

## What this archive claims, and what it does not

**It does:**

- store 537 records with their source, rights and preservation history
- search them by keyword, vector similarity and extracted entities
- answer questions strictly from retrieved passages, citing each one
- show, for every passage, whether it has been checked against an original
- serve an archivist interface for review, publication, OCR correction and
  source verification
- run offline, as a kiosk, with no network and no accelerator

**It does not, yet:**

- quote Dr. Ambedkar. Every imported record is a third-party transcription marked
  `unverified_secondary`, reproduced so it can be read and searched but not cited
  as his words
- claim a page number for continuous text. Where the source has no pagination the
  archive says so: *Page information unavailable in indexed source*
- invent a date. A record known only to a year keeps `date_precision=year`
- publish a knowledge graph or narrative stories. Both are listed in the
  navigation and explain that they are not built yet, rather than being hidden
  until they work
- translate the corpus. The interface is in English, Hindi and Marathi; the
  archival text is shown as transcribed. See
  [why](docs/HONESTY.md#why-the-corpus-is-not-translated)

The verification workflow exists and is unused: `scripts/verify_source.py`
compares a record against its cited source and only promotes it to
`verified_primary` on a character-level match, with a named reviewer. Most cited
sources are behind Cloudflare challenges, so it has refused every attempt so far.
That refusal is the correct outcome, and no record has been promoted.

---

## Honesty rules, and how they are enforced

| Rule | Enforced by |
| --- | --- |
| Unverified text is never shown in quotation marks | Answer formatting; `test_answer_from_unverified_text_is_labelled_not_grounded` |
| A citation never claims verification its evidence lacks | Citation builder; `test_unverified_text_is_not_quoted` |
| Missing pagination is declared, not blank | Public text endpoint; `test_unpaginated_text_reports_no_page` |
| Dates are never invented to fill a gap | Import precision; `test_year_only_record_keeps_year_precision` |
| The archive refuses questions it cannot support | Relevance and groundedness gates; `test_unanswerable_question_is_refused` |
| A refusal says why, in prose | `refusal_reason`; `refusal_code` kept for logs |
| An unknown verification status reads as unknown | `verificationLabel`; tested in all three languages |
| Drafts are invisible to the public | `_load_document`; `test_unpublished_record_is_not_public` |
| No admin route answers an anonymous caller | Router-level permission gate; `test_admin_routes_require_authentication` |
| Every public payload declares its verification status | `DocumentSummary.verification_status`, required with no default |
| The corpus is never machine-translated into a citation | `machine_translation` status; see `docs/HONESTY.md` |

`apps/web/scripts/check-contract.mjs` re-checks these against a running server,
including that an unverified answer contains no quotation marks and that an
unrelated question is refused.

---

## Architecture

```
apps/api      FastAPI, SQLAlchemy 2, Alembic, PostgreSQL (pgvector + full text)
apps/web      React 18, TypeScript, Vite, Tailwind, React Router
apps/kiosk    Electron shell, HW_* hardware profiles
deploy        nginx config, database init
docs          architecture, honesty policy, operations runbook, Render guide
render.yaml   Render blueprint: database, web service, worker
scripts       Development helpers, including the detached API launcher
```

Retrieval is hybrid: PostgreSQL full-text search and vector search are fused
(RRF), reranked, and filtered by a question-overlap and groundedness gate. In the
default offline configuration the "LLM" is extractive — it selects and orders
sentences that already exist in the archive rather than writing new text — and
embeddings are deterministic hashing vectors, so the whole system runs with no
external service and gives the same answer every time.

Degraded modes are first-class, not afterthoughts: object storage falls back to
the filesystem, the knowledge graph falls back to a relational mirror, the queue
falls back to the database, and `/api/v1/health` reports which backend is live.

### Data model

```
ANSWER → CHUNK → DOCUMENT → PAGE → ORIGINAL SOURCE
```

A chunk is a retrieved span of text and carries its own `quote_verified` flag. A
document carries `verification_status`. These are deliberately separate: a page
can have accurate OCR and still be a modern paraphrase that was never compared
with an archival original.

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Running it

Requires PostgreSQL 16 with `vector` and `pg_trgm`, Node 22, and Python 3.12+.

```bash
# Backend — note the path: from apps/api the virtualenv is at ../../.venv
python3 -m venv .venv
.venv/bin/pip install -r apps/api/requirements.txt
cd apps/api && ../../.venv/bin/python -m alembic upgrade head && cd ../..

# API on 127.0.0.1:8099
python3 scripts/dev_api.py

# Worker — required, or health stays degraded and nothing ingests
cd apps/api && ../../.venv/bin/python -m app.worker

# Frontend
cd apps/web && npm install && npm run dev   # serves on 5173, proxies /api
```

Import the corpus (this truncates and rebuilds the corpus tables):

```bash
cd apps/api
../../.venv/bin/python scripts/import_corpus.py --path data/speeches.jsonl --publish
```

Create a user for the administrative routes. There is no default password
anywhere in this repository:

```bash
../../.venv/bin/python scripts/create_user.py \
  --email you@example.org --password 'choose-something' --role SUPER_ADMIN
```

`data/speeches.jsonl` holds the 537 records, with its source, licence and
checksum documented in `apps/api/data/README.md`.

---

## The kiosk

```bash
cd apps/kiosk && npm install
HW_KIOSK_MODE=true HW_SPEAKER_ENABLED=true npm start
```

`HW_*` environment variables describe the venue's hardware — fullscreen mode,
idle timeout, scanner, microphone, speaker, camera, and minimum touch target — so
the same build works on a phone and a 43-inch display. The rules are pure
functions and are unit-tested without launching Electron.

If `npm install` does not fetch the Electron binary, see
[docs/OPERATIONS.md](docs/OPERATIONS.md#the-kiosk).

---

## Tests

```bash
# Backend: 106 tests. Needs PostgreSQL; uses a disposable migrated database.
cd apps/api && ../../.venv/bin/python -m pytest tests -q

# Frontend: 54 tests, strict typecheck, production build
cd apps/web && npm run test && npm run typecheck && npm run build

# Kiosk: 21 tests
cd apps/kiosk && npm test

# Browser checks. These need the API on 8099 and `npm run preview` on 4173,
# or any single-port server — see below.
cd apps/web
npm run contract       # 31 checks against a running API
npm run check:routes   # 84 checks: every public route, in all three languages
npm run check:i18n     # 13 checks: the language control and its persistence
npm run check:offline  # 16 checks: the service worker, with the browser offline

# Needs a test account; see docs/OPERATIONS.md
DHA_ADMIN_EMAIL=… DHA_ADMIN_PASSWORD=… npm run check:admin   # 34 checks
```

Every browser check takes a `WEB_URL`, so it can be pointed at a single-port
deployment rather than the preview server — the interface and the API are then
the same origin, which is how Render serves them:

```bash
WEB_DIST=../apps/web/dist python -m uvicorn app.main:app --port 8094   # apps/api
cd ../web
WEB_URL=http://127.0.0.1:8094 npm run check:routes
DHA_API_URL=http://127.0.0.1:8094/api/v1 npm run contract
```

Note that `check:admin` creates and modifies users in whatever database it is
pointed at. Run it against a development archive, never a live one.

All browser checks default to the preview server on **4173**, not the dev
server: the service worker is only registered in a production build, so a check
pointed at 5173 would depend on state left over in a browser profile from an
earlier run.

The backend suite runs against a real PostgreSQL database, created and dropped
around each run, and builds its schema with the Alembic migrations. SQLite cannot
render `TSVECTOR` or `Vector`, so it is not used. See
`apps/api/tests/README.md`.

Several tests were confirmed to fail when their target is deliberately broken.
A test that has never been seen to fail is not evidence of anything.

The worker path is covered too, against both queue backends. It was not
exercised at all until the worker was actually run, which is how four separate
faults survived in it — a crash on startup, a crash whenever the queue drained,
and two wrong assumptions about rq's API. Each of those is now a failing test
when reintroduced.

---

## Deployment

### On Render

Push this repository, then in Render choose **New → Blueprint** and point it at
it. [`render.yaml`](render.yaml) declares all three resources: a PostgreSQL
database, a web service, and a background worker. Set `PUBLIC_BASE_URL` to the
address the service is given; Render prompts for it.

The signing secret and the database password are generated by Render. Neither is
in this repository, and a deployment that is missing a real `JWT_SECRET` refuses
to start rather than coming up quietly signable by anyone.

Full instructions, the free tier's costs, and a troubleshooting table are in
[docs/RENDER.md](docs/RENDER.md).

### Locally, with Docker

```bash
cp .env.example .env    # then set JWT_SECRET and POSTGRES_PASSWORD
docker compose up -d --build
```

`docker compose` puts nginx in front of the API, which is the better arrangement
on a machine you control: it terminates TLS, serves assets without waking Python,
and keeps uploads on a named volume. Render cannot do that — it gives a service
one port — which is why `Dockerfile.render` exists and FastAPI serves the built
interface itself when `WEB_DIST` is set.

**`docker compose up` has never been run.** Docker was not available on the
machine this was built on, so both images are written to be read and checked,
not trusted. `docs/OPERATIONS.md#deployment` lists what to verify first.

What *has* been verified is the single-port arrangement, which is the part
Render depends on: the built interface, the API and the health check all answer
on one port, hashed assets are cached immutably, `sw.js` and `offline.html` are
not, unknown API paths are 404s rather than pages of HTML, and requests cannot
escape the build directory. See
[`apps/api/tests/test_spa.py`](apps/api/tests/test_spa.py).

---

## Current state

Built and working: ingestion, chunking, hybrid search, RAG with refusal, graph
extraction, preservation and versioning, OCR plumbing, auth with role-based
permissions, audit logging, background jobs, the public interface, the archivist
interface, the offline service worker, the Electron kiosk shell, and a
trilingual interface.

The worker runs and completes real jobs through both queue backends. It is not
started automatically: run `python -m app.worker` in a second terminal, or
`python -m app.worker --once` to drain a single job.

Known gaps, in the order they matter:

1. **No record is verified.** The workflow exists; the sources are unreachable.
2. **No source has been promoted**, so the quotation path is untested against
   real verified text. A test-only fixture covers the logic.
3. **Docker is unverified.** The files exist; they have never been run.
4. **Knowledge Graph and Stories are placeholders.** The navigation is fixed by
   the brief and is not reshaped as features land.

---

## Documentation

- [docs/HONESTY.md](docs/HONESTY.md) — what the archive may claim, and why
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how it is built, and the
  degraded modes
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — running, testing, deploying,
  verifying a source, and what has not been checked
- [docs/RENDER.md](docs/RENDER.md) — deploying to Render, the one-port
  arrangement, and the free tier's costs
- [apps/api/data/README.md](apps/api/data/README.md) — the corpus, its licence
  and its checksum
- [apps/api/tests/README.md](apps/api/tests/README.md) — the test database

---

## Licence and attribution

Code in this repository is provided for the SIH 2026 problem. The corpus is
third-party data under Apache-2.0, attributed in `apps/api/data/README.md`, and
remains the property of its authors. Nothing here grants a right to quote Dr.
B. R. Ambedkar: that belongs to the rights holders of the original works, which
are named per record.
