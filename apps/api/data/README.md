# Corpus

`speeches.jsonl` is the 537-record speech corpus this archive was built from.
It is kept here, rather than only in a download directory, so that a re-import
can be reproduced and checked against the same bytes.

| | |
| --- | --- |
| Source | `sanjeevafk/ambedkar-speech-corpus` on Hugging Face |
| URL | https://huggingface.co/datasets/sanjeevafk/ambedkar-speech-corpus |
| Licence | Apache-2.0 |
| Records | 537 |
| SHA-256 | `b9806ef55e32ecc1577eed18c85c305778add6c73832b425ea77e6b9e87b9e7b` |

Check the file before importing it:

```bash
shasum -a 256 data/speeches.jsonl
```

## What this data is, and what it is not

The records are a **third-party transcription and summary**, not a photographic
or archival source. They are imported as `unverified_secondary`: readable and
searchable, never quotable as Dr. Ambedkar's words, and never promoted to
`verified_primary` without a character-level check against the cited source.

Each record carries a `source_url` and `speech_id` (for example
`SPEECH-BRA-1948-02`) that identify where its text should be verified. Many of
those URLs are behind Cloudflare challenges, which is why no record has been
promoted yet. `scripts/verify_source.py` performs the check when a source can be
retrieved.

The importer strips the generated title block, front matter and `## TL;DR`
section before indexing, and preserves both the raw record and the cleaned text
as document versions. See `scripts/import_corpus.py` for what it does and does
not claim.

## Re-importing

```bash
cd apps/api
../../.venv/bin/python scripts/import_corpus.py --path data/speeches.jsonl --publish
```

This truncates and rebuilds the corpus tables. Read the script's docstring
first.
