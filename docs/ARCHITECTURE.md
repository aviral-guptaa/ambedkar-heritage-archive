# Architecture

## What this is

A digital heritage archive of Dr. B. R. Ambedkar's speeches, built as an
offline-capable kiosk for Smart India Hackathon 2026, problem
[SIH26096](https://sih.decodex.live/sih2026/SIH26096) (category: Hardware,
theme: Smart Education).

It has a public reading interface, an archivist interface, an offline service
worker, and an Electron kiosk shell. Its defining constraint is that it must not
make claims about Dr. Ambedkar's words that it cannot support; see
[HONESTY.md](HONESTY.md), which governs everything here.

## Shape

```
                       ┌──────────────┐
   reader ────────────▶│  web (nginx) │  static interface + /api proxy
                       └──────┬───────┘
                              │
                       ┌──────▼───────┐
                       │     api      │  FastAPI
                       └──┬────────┬──┘
                          │        │
        ┌─────────────────┘        └──────────────┐
        │                                        │
┌───────▼────────┐                     ┌─────────▼────────┐
│   PostgreSQL   │                     │  worker (RQ)     │
│   + pgvector   │◀────────────────────│  ingest/OCR/embed │
└────────────────┘                     └──────────────────┘
        ▲
        │  object store (filesystem or S3)
┌───────┴────────┐
│  source files  │  images and scans, never regenerated
└────────────────┘
```

## Why these choices

**A single nginx in front of the API.** A kiosk is configured once. Two ports
means CORS configuration, and a kiosk at a venue does not get that right. One
port also puts the API inside the service worker's scope, so it can cache
archive responses and not the admin ones.

**PostgreSQL with pgvector, required rather than optional.** Search degrades to
lexical when the vector extension is missing, and the health endpoint says so.
The schema itself needs the extension to exist at migration time, so it is
created by an init script before the API starts.

**The worker runs the same image as the API.** A queued job cannot then behave
differently from the code that enqueued it, which is a class of bug that is very
hard to diagnose from a log.

**Redis is not persisted.** The queue is rebuildable from the database. A stale
queued job surviving a crash is worse than a job that has to be requeued on
purpose.

**Provenance is a chain, not a column.**

```
ANSWER → CHUNK → DOCUMENT → PAGE → ORIGINAL SOURCE
```

A component that displays text receives a type that carries its verification
status, so displaying a passage and declaring what it is worth are the same
act. There is no way to render text in this interface without the means to say
how much it should be trusted.

**Verification is a separate act from publication.** A document can be
published for reading while remaining unverified; those are independent states.
`verify_source.py` is the only path to `verified_primary`, and it requires a
successful fetch of a source that contains a given quote.

**The interface is translated; the corpus is not.** See the reasoning in
[HONESTY.md](Why-the-corpus-is-not-translated). The strings live in
`apps/web/src/lib/i18n.ts` so that a translation of a warning is reviewable in
the repository rather than arriving from a service at runtime — which matters
most for a kiosk with no reliable network.

**The kiosk renders untrusted content.** Context isolation is on, Node
integration is off, the sandbox is on, navigation is restricted to the archive's
own origin, and the preload bridge exposes exactly two functions. This is why
the archive can be pointed at a corpus it did not author.

## Degraded modes

A kiosk may have no network and no accelerator. The archive is expected to run,
and to say what it has lost:

| Missing | Behaviour | Reported as |
| --- | --- | --- |
| Network | Cached records and the offline notice | `offline: true` in health |
| pgvector | Lexical search over tsvector/trigram | `degraded_modes: ["vector_search"]` |
| Object store | Files on local disk | `degraded_modes: ["object_store"]` |
| LLM provider | Extractive answers from retrieved passages | `degraded_modes: ["llm"]` |
| Worker | Jobs queued but not running | `status: "degraded"`, `workers_alive: 0` |

`degraded` is a real state, not an error. A kiosk that answers from a lexical
index and says so is more useful than one that refuses to start.

## Layout

```
apps/api/          FastAPI service, Alembic migrations, tests
  app/api/         route modules
  app/models/      SQLAlchemy models
  app/services/    RAG, verification, graph, storage
  app/providers/   job queue backends
  alembic/         migrations
  scripts/         importer, user creation, source verification
  tests/           pytest, real PostgreSQL fixtures
apps/web/          React interface
  src/components/  public interface
  src/admin/       archivist interface
  src/lib/         API client, formatting, i18n, offline, kiosk bridge
  scripts/         contract, offline and i18n browser checks
  public/          service worker, offline page
apps/kiosk/        Electron shell and hardware profiles
deploy/            nginx config, database init
docs/              this directory
```

## The kiosk's hardware profile

A venue's machine is described by environment variables, so the same build works
on a phone and a 43-inch display:

| Variable | Effect |
| --- | --- |
| `HW_KIOSK_MODE` | fullscreen, no chrome, no devtools |
| `HW_KIOSK_IDLE_TIMEOUT_SECONDS` | returns home after inactivity |
| `HW_KIOSK_RETURN_HOME` | whether that return happens at all |
| `HW_SPEAKER_ENABLED` | allows speaker output |
| `HW_CAMERA_ENABLED` | allows camera access |
| `HW_MICROPHONE_ENABLED` | allows microphone access |
| `HW_SCANNER_ENABLED` | allows a connected scanner |
| `HW_TOUCH_MIN_TARGET_PX` | minimum size of an interactive element |

The rules are pure functions in `apps/kiosk/hardware.js` and are unit-tested
without launching Electron, so a hardware profile can be checked in CI.

## Known limitations

- **No record is verified.** Blocked on archival sources returning Cloudflare
  challenges. See [OPERATIONS.md](OPERATIONS.md#verifying-a-source).
- **Docker is unverified.** Docker was unavailable on the machine this was
  built on. `docker compose up` has never been run.
- **Knowledge Graph and Stories are placeholders.** The navigation is fixed by
  the brief and is not reshaped as features land, so the entries say plainly
  that they are not yet available.
- **The API's own unit tests require PostgreSQL.** SQLite cannot render
  `TSVECTOR` or `Vector`, so the suite uses disposable migrated databases rather
  than pretending SQLite is equivalent.
