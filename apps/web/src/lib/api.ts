/**
 * Typed client for the archive API.
 *
 * Two rules shape this file:
 *
 * 1. Provenance travels with the data. Every type that can carry document text
 *    carries its verification status, so a component cannot display text without
 *    also having the means to say how much it is worth.
 * 2. A missing page is reported, never invented. `pageNumber` and
 *    `pageInformationUnavailable` come straight from the API, and the interface
 *    is expected to render the second when the first is null.
 */

import { translate, type StringKey } from './i18n'

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/v1'

export type VerificationStatus = 'verified_primary' | 'unverified_secondary' | 'unverified_image'

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

export class ApiError extends Error {
  readonly status: number
  readonly requestId: string | undefined
  constructor(message: string, status: number, requestId?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.requestId = requestId
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  // A FormData body must keep the boundary the browser generates, so the
  // content type is only set for the bodies that are actually JSON. Setting it
  // on a multipart body strips the boundary and the server sees no file at all.
  const isMultipart = typeof FormData !== 'undefined' && init?.body instanceof FormData
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: isMultipart
        ? (init?.headers ?? {})
        : { 'content-type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch (cause) {
    // A network failure is reported as such. It must never be rendered as
    // "no results", which would read as a claim about the archive's contents.
    throw new ApiError(
      'The archive service could not be reached. Check your connection and try again.',
      0,
    )
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`
    let requestId: string | undefined
    try {
      const body = (await response.json()) as { detail?: string; request_id?: string }
      if (body.detail) detail = body.detail
      requestId = body.request_id
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(detail, response.status, requestId)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

type QueryValue = string | number | boolean | undefined | null | Array<string | number>

function query(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    // Repeated keys rather than a joined string, so a list filter stays a list.
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, String(item))
    } else {
      search.set(key, String(value))
    }
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

// --------------------------------------------------------------------------
// types
// --------------------------------------------------------------------------

export interface DocumentSummary {
  id: string
  slug: string
  title: string
  author: string | null
  document_type: string
  language: string
  year: number | null
  document_date: string | null
  date_precision: string | null
  collection: string | null
  source_name: string | null
  source_url: string | null
  source_tier: string | null
  page_count: number
  chunk_count: number
  word_count: number
  ocr_status: string
  publication_status: string
  verification_status: VerificationStatus
  is_demo: boolean
  thumbnail_url: string | null
}

export interface DocumentDetail extends DocumentSummary {
  summary: string | null
  location: string | null
  venue: string | null
  source_reference: string | null
  rights: string | null
  provenance: string | null
  editorial_note: string | null
  byte_size: number | null
  checksum_sha256: string | null
  storage_key: string | null
  processing_state: string
  embedding_status: string
  graph_status: string
  created_at: string | null
  updated_at: string | null
  collection_id: string | null
  topics: DocumentTopicLink[]
  people: DocumentPersonLink[]
  events: DocumentEventLink[]
  metadata: Record<string, string>
  integrity: Array<Record<string, unknown>>
  versions: Array<Record<string, unknown>>
}

export interface DocumentTopicLink {
  topic_id: string
  name: string | null
  slug: string | null
  weight: number | null
  source: string | null
}

export interface DocumentPersonLink {
  person_id: string
  name: string | null
  role: string | null
}

export interface DocumentEventLink {
  event_id: string
  title: string | null
  date: string | null
  relation: string | null
}

export interface TextPart {
  index: number
  section: string | null
  language: string
  page_number: number | null
  page_information_unavailable: boolean
  text: string
}

/**
 * The 30-second understanding of one record.
 *
 * `available` is false when the record has too little text to summarise without
 * inventing something; `unavailable_reason` then explains why, and the reader is
 * pointed at the full text instead.
 */
export interface GroundedSummary {
  document_id: string
  document_slug: string
  available: boolean
  disclosure: string
  /** Always `extractive`: the text is quoted from the record, not generated. */
  method: 'extractive'
  model_id: null
  /** One catalogued sentence about what the record is. */
  what_is_this: string
  main_idea: string | null
  key_points: Array<{ text: string; page_number: number | null; chunk_index: number }>
  source_characters: number
  source_chunks: number
  unavailable_reason: string | null
  citations: Array<{ text: string; page_number: number | null }>
}

export interface DigitizeCapabilities {
  ocr_available: boolean
  ocr_detail: string
  translation_available: boolean
  translation_detail: string
  accepted_extensions: string[]
  max_upload_bytes: number
  max_pages: number
  languages: string[]
}

/** The honest outcome of one digitisation attempt. */
export interface DigitizeResult {
  document_id: string
  document_slug: string
  title: string
  filename: string
  sha256: string
  page_count: number
  ocr_status: string
  ocr_engine: string | null
  confidence: number | null
  detected_language: string | null
  /** Text as OCR read it, in the original language. */
  original_text: string
  /** Only ever populated by a real translation provider. */
  english_text: string | null
  translation_status: 'complete' | 'unavailable' | 'failed' | 'not_attempted'
  translation_detail: string | null
  warnings: string[]
  is_draft: boolean
  disclosure: string
}

export interface DocumentText {
  document_id: string
  slug: string
  title: string
  language: string
  verification_status: VerificationStatus
  quote_verified: boolean
  provenance_warning: string | null
  source_url: string | null
  source_reference: string | null
  total: number
  parts: TextPart[]
}

export interface SearchSnippetSegment {
  text: string
  match: boolean
}

export interface SearchSnippet {
  text: string
  highlighted: SearchSnippetSegment[]
}

export interface SearchHit {
  chunk_id: string
  document_id: string
  document_title: string
  slug: string | null
  score: number
  page_number: number | null
  page_information_unavailable: boolean
  section: string | null
  language: string
  snippet: SearchSnippet
  source_url: string | null
  source_reference: string | null
  source_name: string | null
  source_tier: string | null
  document_type: string | null
  year: number | null
  verification_status: VerificationStatus
  quote_verified: boolean
  provenance_warning: string | null
}

export interface SearchResponse {
  query: string
  mode: string
  total: number
  results: SearchHit[]
  diagnostics: Record<string, unknown>
  suggestions: string[] | null
  notices: string[]
}

export interface Citation {
  /** Numeric marker matching the bracketed numbers in the answer text. */
  marker: number
  chunk_id: string
  document_id: string
  document_title: string
  page_number: number | null
  page_information_unavailable: boolean
  source_url: string | null
  source_reference: string | null
  quote: string
  verified: boolean
  verification_status: VerificationStatus
  provenance_warning: string | null
}

export interface RagEvidence {
  chunk_id: string
  document_id: string
  document_title: string
  slug: string | null
  page_number: number | null
  page_information_unavailable: boolean
  section: string | null
  snippet: string
  score: number
  source_url: string | null
  source_reference: string | null
  source_name: string | null
  source_tier: string | null
  document_type: string | null
  year: number | null
  language: string
  retrieval: string
  verification_status: VerificationStatus
  quote_verified: boolean
  provenance_warning: string | null
}

export interface RagClaim {
  text: string
  kind: string
  markers: number[]
  supported: boolean
}

export interface RagProviders {
  llm: string
  model: string
  retrieval: string
}

export interface RagAnswer {
  query_id: string
  answer: string
  answer_kind: 'grounded' | 'unverified_summary' | 'insufficient_evidence' | 'conflicting' | 'refused'
  answer_language: string
  refused: boolean
  /** A sentence safe to show a reader; null while an answer is being produced. */
  refusal_reason: string | null
  /** The machine-readable reason, for logging rather than display. */
  refusal_code: string | null
  groundedness: number
  evidence: RagEvidence[]
  citations: Citation[]
  claims: RagClaim[]
  conflicting_evidence: boolean
  conflict_note: string | null
  providers: RagProviders
  duration_ms: number
  disclaimer: string
}

export interface TimelineEvent {
  id: string
  title: string
  event_date: string | null
  year: number | null
  date_precision: string
  description: string | null
  place: string | null
  event_type: string | null
  document_count: number
  verification_status: VerificationStatus
  is_demo: boolean
}

export interface TimelineResponse {
  events: TimelineEvent[]
  year_from: number | null
  year_to: number | null
  total: number
  years_with_no_data: number[]
  notices: string[]
}

export interface FacetValue {
  value: string
  label: string
  count: number
}

export interface Facets {
  languages: FacetValue[]
  document_types: FacetValue[]
  year_range: { from: number | null; to: number | null }
}

export interface ArchiveStats {
  documents_total: number
  documents_published: number
  chunks: number
  embedded_chunks: number
  events: number
  media_items: number
  graph_nodes: number
  graph_edges: number
  rag_queries: number
  answered_queries: number
  unverified_answers: number
  integrity: unknown | null
  computed_at: string
}

export interface MediaItem {
  id: string
  title: string
  kind: string
  url: string | null
  thumbnail_url: string | null
  document_id: string | null
  verification_status: VerificationStatus | null
}

export interface MediaResponse {
  items: MediaItem[]
  total: number
  notices: string[]
}

export interface NotAvailableResponse {
  detail?: string
  notices?: string[]
  total?: number
}

// --------------------------------------------------------------------------
// endpoints
// --------------------------------------------------------------------------

export const api = {
  stats: () => request<ArchiveStats>('/stats'),

  health: () => request<Record<string, unknown>>('/health'),

  documents: (params: {
    limit?: number
    offset?: number
    q?: string
    document_type?: string
    year?: number
    year_from?: number
    year_to?: number
    language?: string
    source_id?: string
  }) => request<Page<DocumentSummary>>(`/documents${query(params)}`),

  document: (identifier: string) => request<DocumentDetail>(`/documents/${identifier}`),

  documentText: (identifier: string) => request<DocumentText>(`/documents/${identifier}/text`),

  documentSummary: (identifier: string) => request<GroundedSummary>(`/documents/${identifier}/summary`),

  facets: () => request<Facets>('/documents/facets'),

  search: (payload: {
    query: string
    limit?: number
    offset?: number
    document_type?: string
    year_from?: number
    year_to?: number
    language?: string
    verification_status?: string
    mode?: 'hybrid' | 'vector' | 'lexical'
  }) => request<SearchResponse>('/search', { method: 'POST', body: JSON.stringify(payload) }),

  ask: (question: string, options?: { filters?: Record<string, unknown> }) =>
    request<RagAnswer>('/rag/ask', {
      method: 'POST',
      body: JSON.stringify({ question, filters: options?.filters ?? {}, persist: false }),
    }),

  /**
   * Ask about one record only.
   *
   * `document_ids` is sent at the top level because that is the field the
   * server scopes retrieval with; sending it nested under `filters` would be
   * silently ignored and the answer would quietly come from the whole archive.
   */
  askAboutDocument: (question: string, documentId: string) =>
    request<RagAnswer>('/rag/ask', {
      method: 'POST',
      body: JSON.stringify({
        question,
        document_ids: [documentId],
        mode: 'grounded',
        persist: false,
      }),
    }),

  digitizeCapabilities: () => request<DigitizeCapabilities>('/digitize/capabilities'),

  digitize: (file: File, options?: { title?: string; sourceLanguage?: string }) => {
    const form = new FormData()
    form.append('file', file)
    if (options?.title) form.append('title', options.title)
    if (options?.sourceLanguage) form.append('source_language', options.sourceLanguage)
    return request<DigitizeResult>('/digitize', { method: 'POST', body: form })
  },

  timeline: (params?: { year_from?: number; year_to?: number; event_type?: string[]; limit?: number }) =>
    request<TimelineResponse>(`/timeline${query(params ?? {})}`),

  topics: () => request<Array<{ slug: string; name: string; count: number; summary?: string | null }>>('/topics'),

  constitutionalArticles: () =>
    request<Array<{ article_number: string; title: string; mentions: number; document_slugs?: string[] }>>(
      '/constitutional-articles',
    ),

  media: (params?: { kind?: string; limit?: number; offset?: number }) =>
    request<MediaResponse>(`/media${query(params ?? {})}`),

  stories: () => request<NotAvailableResponse>('/stories'),
}

/**
 * A short, honest label for a verification status.
 *
 * The status is data from the server, but the sentence a reader sees is not:
 * it is the interface's, and it is translated. The unrecognised case is the
 * important one — a status this build has never seen is described as unknown
 * rather than passing through, so a new backend status cannot slip through
 * looking like an ordinary one.
 */
export function verificationLabel(
  status: VerificationStatus | string | null | undefined,
  t: (key: StringKey) => string = (key) => translate(key),
): string {
  const keys: Record<string, StringKey> = {
    verified_primary: 'verification.titleVerified',
    unverified_secondary: 'verification.titleSecondary',
    unverified_image: 'verification.titleImage',
    machine_translation: 'verification.titleTranslation',
    pending_review: 'verification.titlePending',
  }
  return t(keys[String(status)] ?? 'verification.titleUnknown')
}
