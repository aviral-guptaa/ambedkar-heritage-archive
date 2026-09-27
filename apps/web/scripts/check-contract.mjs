/**
 * Contract and honesty check for the public API.
 *
 * Two things this guards against:
 *
 *  1. Drift. The pages read specific fields. A backend rename silently turns a
 *     rendered field into `undefined` at runtime, which TypeScript cannot catch
 *     when the interface was written by hand. Every required key below is a
 *     field a page actually dereferences.
 *
 *  2. Dishonesty. The rules that make this archive trustworthy are asserted
 *     here as executable checks: unverified text is never presented as a
 *     quotation, an unverified answer never carries a verified citation, and
 *     questions the archive cannot support are refused rather than answered.
 *
 * Requires a running API. Override the base URL with DHA_API_URL.
 *
 *   npm run contract
 */
import process from 'node:process'

const BASE = process.env.DHA_API_URL ?? 'http://127.0.0.1:8099/api/v1'

const failures = []
const passes = []

function check(name, condition, detail) {
  if (condition) passes.push(name)
  else failures.push(detail ? `${name} — ${detail}` : name)
}

async function get(path) {
  const response = await fetch(`${BASE}${path}`)
  if (!response.ok) throw new Error(`GET ${path} -> ${response.status}`)
  return response.json()
}

async function post(path, body) {
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`POST ${path} -> ${response.status}`)
  return response.json()
}

function hasKeys(name, payload, keys) {
  const missing = keys.filter((key) => !(key in payload))
  check(name, missing.length === 0, missing.length ? `missing ${missing.join(', ')}` : undefined)
}

// ---------------------------------------------------------------- contracts --

const stats = await get('/stats')
hasKeys('stats fields', stats, [
  'documents_published',
  'chunks',
  'events',
  'media_items',
  'graph_nodes',
  'graph_edges',
  'unverified_answers',
  'computed_at',
])

const listing = await get('/documents?limit=5')
hasKeys('document list fields', listing, ['items', 'total', 'limit', 'offset'])
hasKeys(
  'document summary fields',
  listing.items[0] ?? {},
  [
    'slug',
    'title',
    'author',
    'document_type',
    'language',
    'year',
    'document_date',
    'date_precision',
    'source_name',
    'source_url',
    'source_tier',
    'page_count',
    'word_count',
    'verification_status',
    'is_demo',
  ],
)
check(
  'document list carries a verification status',
  (listing.items[0] ?? {}).verification_status !== undefined,
  'the UI would have to guess whether the text may be quoted',
)

const slug = listing.items[0]?.slug
if (!slug) throw new Error('the archive returned no published documents to check')

const detail = await get(`/documents/${slug}`)
hasKeys('document detail fields', detail, [
  'summary',
  'rights',
  'provenance',
  'source_reference',
  'checksum_sha256',
  'processing_state',
  'graph_status',
  'verification_status',
  'topics',
  'people',
  'events',
  'metadata',
  'integrity',
  'versions',
])
check(
  'document detail exposes verification status',
  typeof detail.verification_status === 'string' && detail.verification_status.length > 0,
)

const text = await get(`/documents/${slug}/text`)
hasKeys('document text fields', text, [
  'slug',
  'language',
  'verification_status',
  'quote_verified',
  'provenance_warning',
  'total',
  'parts',
])
for (const part of text.parts ?? []) {
  hasKeys('text part fields', part, [
    'index',
    'section',
    'language',
    'page_number',
    'page_information_unavailable',
    'text',
  ])
  check(
    'a missing page is declared, not left blank',
    part.page_number !== null || part.page_information_unavailable === true,
    `part ${part.index} has neither a page number nor page_information_unavailable`,
  )
}

const facets = await get('/documents/facets')
hasKeys('facet fields', facets, ['languages', 'document_types', 'year_range'])

const search = await post('/search', { query: 'constitution', limit: 5 })
hasKeys('search fields', search, ['query', 'mode', 'total', 'results', 'diagnostics', 'notices'])
hasKeys('search hit fields', search.results[0] ?? {}, [
  'chunk_id',
  'document_id',
  'document_title',
  'slug',
  'score',
  'page_number',
  'page_information_unavailable',
  'snippet',
  'verification_status',
  'quote_verified',
  'provenance_warning',
])
hasKeys('search snippet fields', search.results[0]?.snippet ?? {}, ['text', 'highlighted'])

const timeline = await get('/timeline?limit=2000')
hasKeys('timeline fields', timeline, [
  'events',
  'year_from',
  'year_to',
  'total',
  'years_with_no_data',
  'notices',
])
hasKeys('timeline event fields', timeline.events[0] ?? {}, [
  'id',
  'title',
  'event_date',
  'year',
  'date_precision',
  'event_type',
  'document_count',
  'verification_status',
  'is_demo',
])
check(
  'timeline events no longer report a bare "verified" flag',
  !('verified' in (timeline.events[0] ?? {})),
  'a field named "verified" that means "not a demo record" invites the wrong conclusion',
)

const media = await get('/media?limit=5')
hasKeys('media fields', media, ['items', 'total', 'limit', 'offset'])

// ----------------------------------------------------------------- honesty --

const RELEVANT = 'What did Ambedkar say about endogamy and exogamy?'
const IRRELEVANT = ['What is the weather in Paris tomorrow?', 'Who won the 2019 cricket world cup?']
const QUOTE_MARKS = ['"', '“', '”', '«', '»']

const answer = await post('/rag/ask', { question: RELEVANT })
hasKeys('rag answer fields', answer, [
  'query_id',
  'answer',
  'answer_kind',
  'answer_language',
  'refused',
  'refusal_reason',
  'groundedness',
  'evidence',
  'citations',
  'claims',
  'conflicting_evidence',
  'conflict_note',
  'providers',
  'duration_ms',
  'disclaimer',
])
hasKeys('rag citation fields', answer.citations[0] ?? {}, [
  'marker',
  'document_title',
  'page_number',
  'page_information_unavailable',
  'source_url',
  'source_reference',
  'quote',
  'verified',
  'verification_status',
  'provenance_warning',
])

check(
  'a supported question is answered from the archive',
  answer.answer_kind === 'grounded' || answer.answer_kind === 'unverified_summary',
  `answer_kind was ${answer.answer_kind}`,
)
check('the answer cites its evidence', (answer.citations ?? []).length > 0)

if (answer.answer_kind === 'unverified_summary') {
  check(
    'an unverified summary contains no quotation marks',
    !QUOTE_MARKS.some((mark) => answer.answer.includes(mark)),
    'quotation marks would present unverified text as a transcription',
  )
  const verifiedCitations = answer.citations.filter((citation) => citation.verified)
  check(
    'no citation on an unverified answer claims to be verified',
    verifiedCitations.length === 0,
    `${verifiedCitations.length} citation(s) marked verified against unverified evidence`,
  )
  check(
    'every unverified citation declares its status',
    answer.citations.every(
      (citation) =>
        citation.verification_status === 'unverified_secondary' ||
        citation.verification_status === 'machine_translation' ||
        citation.verification_status === 'pending_review',
    ),
    'a citation without a declared status leaves the reader to guess',
  )
  check(
    'an unverified answer carries a disclaimer',
    typeof answer.disclaimer === 'string' && answer.disclaimer.length > 0,
  )
}

for (const question of IRRELEVANT) {
  const refused = await post('/rag/ask', { question })
  check(`"${question}" is refused rather than answered`, refused.refused === true, `answer_kind ${refused.answer_kind}`)
  check(
    `"${question}" is not given a grounded answer`,
    refused.answer_kind !== 'grounded' && refused.answer_kind !== 'unverified_summary',
    `answer_kind was ${refused.answer_kind}`,
  )
  check(
    `"${question}" states why it was declined`,
    typeof refused.refusal_reason === 'string' && refused.refusal_reason.length > 0,
  )
}

// ------------------------------------------------------------------ report --

for (const name of passes) console.log(`  ok   ${name}`)
for (const failure of failures) console.error(`  FAIL ${failure}`)
console.log(`\n${passes.length} passed, ${failures.length} failed`)
process.exit(failures.length === 0 ? 0 : 1)
