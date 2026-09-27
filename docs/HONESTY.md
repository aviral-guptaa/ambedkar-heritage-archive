# The honesty policy

This is the constraint the whole project is built around, so it is written down
first and everything else is checked against it.

## What the archive contains

Every text in this archive was imported from a third-party dataset:

- `huggingface.co/datasets/sanjeevafk/ambedkar-speech-corpus` (Apache-2.0)
- 537 records, imported unmodified
- SHA-256 `b9806ef55e32ecc1577eed18c85c305778add6c73832b425ea77e6b9e87b9e7b`

That dataset describes its records as official transcriptions. Inspection shows
the text is modern summaries written in the third person — not transcriptions of
anything Dr. Ambedkar said. This is a statement about the dataset, and it is the
reason for everything below.

## The rules

1. **Nothing is presented as a quotation until it has been checked against an
   archival original.** No record in this archive is currently checked. All 537
   carry `unverified_secondary`.

2. **Unverified text is shown as a summary, never inside quotation marks.**
   Adding quotation marks asserts an exact wording that nobody has verified.
   The rendering has no quotation marks, and a test fails if any appear.

3. **A missing page is reported, never inferred.** A record whose source is
   unpaginated says `Page information unavailable in indexed source.` The
   importer stores such records as page `0` internally, but the interface never
   presents that as a real page number.

4. **A missing date is reported at the precision the source records.** A
   year-only record is not given a made-up day. Where the dataset provides
   `09.12.1948`, that is used; where it does not, the interface says the date
   is not recorded.

5. **Every passage links to the source that would be needed to check it.** A
   citation is a path to verification, not a decoration.

6. **An unrecognised verification status is shown as unknown, never as
   verified.** A status the build has not seen is described as unknown so that a
   new backend status cannot pass through looking ordinary.

7. **Translations are of the interface, never of the archival text.** See
   [Why the corpus is not translated](#why-the-corpus-is-not-translated).

8. **A machine translation is never a source of record.** Where a translation
   exists it is labelled `machine_translation` and is not citable.

9. **Degraded operation is stated, not hidden.** With no vector database, no
   object store, or no model provider, the archive falls back to lexical search
   and extractive answers, and the health response says which mode it is in.

## Verification

`apps/api/scripts/verify_source.py` is the only path by which a record can
become `verified_primary`. It fetches the record's own cited source and requires
all of the following, and refuses otherwise:

- a configured source URL that is reachable and returns the expected content;
- whole-document similarity at or above the threshold (default 0.97);
- every sampled passage matching verbatim (default `--sample-threshold 1.0`);
- a named reviewer, recorded in the audit log.

**A failed fetch is never a pass.** The script exits non-zero. This is the most
important behaviour in the project: the archives that hold the originals
(`columbia.edu`, and the Government of India) currently return Cloudflare
challenges, so verification is blocked in practice rather than faked.

Until a human has done that work, the honest state of this archive is: *537
readable, searchable, unverified records, none of which should be quoted as
Dr. Ambedkar's words.*

## Why the corpus is not translated

The interface is available in English, Hindi and Marathi. The archival text is
shown in the language it was transcribed in.

Translating an unverified transcription would place a machine's reading of an
unverified reading between the reader and the source. The result could not
honestly be presented as more authoritative than what is already stored, and it
would make the provenance harder to follow rather than easier. A reader who
does not read the corpus's language is better served by an accurate index, a
clear statement of what the text is, and a link to the original — which is what
this archive provides.

## How these rules are enforced

| Rule | Enforced by |
| --- | --- |
| No quotation marks on unverified text | `apps/web/src/lib/i18n.test.tsx`, `Provenance.tsx` |
| Unverified status never reads as verified | `apps/web/src/components/Provenance.test.tsx` |
| Unknown status reads as unknown | `apps/web/src/lib/api.ts`, tested per language |
| Missing page reported | `apps/api/tests/test_honesty.py`, `apps/web/src/lib/format.test.ts` |
| Date precision respected | `apps/api/tests/test_honesty.py` |
| Failed fetch is not a pass | `apps/api/scripts/verify_source.py` |
| Degraded mode is stated | `apps/api/app/api/health.py` |
| Contract completeness | `apps/web/scripts/check-contract.mjs` |

Several of these are additionally checked by mutation: a deliberately broken
implementation is confirmed to make the relevant test fail. A test that has
never been seen to fail is not evidence of anything.
