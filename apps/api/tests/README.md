# Tests

```bash
cd apps/api
../../.venv/bin/python -m pytest        # whole suite
../../.venv/bin/python -m pytest tests/test_honesty.py -v
```

## Why these tests run against PostgreSQL

The archive stores its text as a `tsvector` for full-text search and its
embeddings in a `pgvector` column. SQLite cannot render either type, so a suite
that ran there would exercise a different product from the one that ships. These
tests therefore create a real PostgreSQL database.

Each run creates a database named `dha_test_<random>`, runs the Alembic
migrations into it — so a broken migration fails the suite — and drops it at the
end. Your real archive database is never opened.

By default the suite connects over the local Homebrew socket as the OS user,
which is a superuser and can create databases:

```bash
DHA_TEST_PG_HOST=/tmp DHA_TEST_PG_DSN="host=/tmp dbname=postgres" pytest
```

Set `DHA_TEST_PG_HOST` to `localhost` to use TCP instead; you will need a role
with `CREATEDB`.

## What `test_honesty.py` is for

The rest of the suite checks that features work. `test_honesty.py` checks the
promises the interface makes to a reader:

| Test | Promise |
| --- | --- |
| `test_unpaginated_text_reports_no_page` | Continuous text never claims a page number |
| `test_public_document_exposes_verification_status` | A client never has to guess whether text may be quoted |
| `test_unverified_text_is_not_quoted` | Unverified text is never presented as a quotation |
| `test_search_results_declare_provenance` | Every search hit declares its own status |
| `test_year_only_record_keeps_year_precision` | A date is never invented to fill a gap |
| `test_unanswerable_question_is_refused` | The archive declines rather than answers from nothing |
| `test_empty_question_is_refused` | An empty question is refused with a stated reason |
| `test_admin_routes_require_authentication` | No admin route answers an anonymous caller |
| `test_unpublished_record_is_not_public` | A draft is invisible to the public by slug or id |

These were checked by deliberately breaking the behaviour they describe — making
the text endpoint claim a known page, marking unverified text quotable, and
defaulting the verification status to `verified_primary` — and confirming that
each mutation failed a test. A test that cannot fail is not doing its job.
