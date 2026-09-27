/**
 * The admin API client.
 *
 * It is kept separate from the public client for one reason: this code holds a
 * staff token. The public pages can be cached and read by anyone, so they must
 * never import anything that touches storage for credentials.
 */

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '/api/v1'

const ACCESS_KEY = 'dha.admin.access'
const REFRESH_KEY = 'dha.admin.refresh'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function readTokens(): { access: string | null; refresh: string | null } {
  return {
    access: sessionStorage.getItem(ACCESS_KEY),
    refresh: sessionStorage.getItem(REFRESH_KEY),
  }
}

function storeTokens(access: string, refresh: string): void {
  // sessionStorage, not localStorage: a closing the browser ends the session,
  // so a shared kiosk machine is not left holding a curator's token.
  sessionStorage.setItem(ACCESS_KEY, access)
  sessionStorage.setItem(REFRESH_KEY, refresh)
}

export function clearTokens(): void {
  sessionStorage.removeItem(ACCESS_KEY)
  sessionStorage.removeItem(REFRESH_KEY)
}

/**
 * Turn a failed response into a sentence a curator can act on.
 *
 * The backend sends `detail` as either a string or a list of validation
 * problems, so both shapes are handled rather than showing "[object Object]".
 */
function describe(status: number, payload: unknown): string {
  const detail = (payload as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const problem = item as { msg?: string; loc?: unknown[] }
        const field = Array.isArray(problem.loc) ? problem.loc.at(-1) : null
        return field ? `${String(field)}: ${problem.msg ?? 'invalid'}` : (problem.msg ?? 'invalid')
      })
      .join('; ')
  }
  if (status === 401) return 'Your session has ended. Sign in again.'
  if (status === 403) return 'Your role does not permit that action.'
  return `Request failed (${status}).`
}

async function parse(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text) as unknown
  } catch {
    return { detail: text }
  }
}

export async function adminRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const { access } = readTokens()
  const headers = new Headers(init.headers)
  if (access) headers.set('Authorization', `Bearer ${access}`)
  if (init.body) headers.set('Content-Type', 'application/json')

  const response = await fetch(`${BASE}${path}`, { ...init, headers })
  if (response.status === 401 && !path.startsWith('/auth/')) {
    // A short-lived access token should be replaced silently, once.
    if (await tryRefresh()) return adminRequest<T>(path, init)
    clearTokens()
    throw new ApiError(401, 'Your session has ended. Sign in again.')
  }
  const payload = await parse(response)
  if (!response.ok) {
    const requestId = response.headers.get('X-Request-ID') ?? undefined
    throw new ApiError(response.status, describe(response.status, payload), requestId)
  }
  return payload as T
}

async function tryRefresh(): Promise<boolean> {
  const { refresh } = readTokens()
  if (!refresh) return false
  try {
    const response = await fetch(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    })
    if (!response.ok) return false
    const tokens = (await response.json()) as { access_token: string; refresh_token: string }
    storeTokens(tokens.access_token, tokens.refresh_token)
    return true
  } catch {
    return false
  }
}

export interface AdminUser {
  id: string
  email: string
  full_name: string | null
  role: string
  is_active: boolean
  created_at: string | null
}

interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: AdminUser
}

export async function login(email: string, password: string): Promise<AdminUser> {
  const response = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  const payload = await parse(response)
  if (!response.ok) throw new ApiError(response.status, describe(response.status, payload))
  const tokens = payload as TokenResponse
  storeTokens(tokens.access_token, tokens.refresh_token)
  return tokens.user
}

export interface Stats {
  documents_total: number
  documents_published: number
  manuscripts: number
  chunks: number
  embedded_chunks: number
  pages: number
  stored_bytes: number
  events: number | null
  media_items: number | null
  stories: number | null
  graph_nodes: number | null
  graph_edges: number | null
}

export interface Job {
  id: string
  job_type: string
  status: string
  priority: number
  progress: number
  payload: Record<string, unknown>
  result: Record<string, unknown> | null
  error: string | null
  attempts: number
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface JobList {
  items: Job[]
  total: number
  queue_depth: number
  workers_alive: number
  backend: string
}

export interface OcrQueueItem {
  document_id: string
  document_title: string
  page_number: number
  ocr_engine: string | null
  ocr_confidence: number | null
  ocr_language: string | null
  document_ocr_status: string
  text: string
  layout: Record<string, unknown> | null
}

export interface RelationshipReview {
  id: string
  relation: string
  from_type: string
  from_id: string
  from_label: string | null
  to_type: string
  to_id: string
  to_label: string | null
  confidence: number | null
  status: string
  created_at: string
  decided_at: string | null
  note?: string | null
}

export interface AuditEntry {
  id: string
  /** Null means a scheduled or system action, which the reader must be able to see as such. */
  actor_id: string | null
  actor_email: string | null
  action: string
  entity_type: string | null
  entity_id: string | null
  detail: Record<string, unknown> | null
  created_at: string
}

export interface RoleDefinition {
  name: string
  description: string | null
  permissions: string[]
}

export interface DocumentRow {
  id: string
  slug: string
  title: string
  author: string | null
  document_type: string
  year: number | null
  document_date: string | null
  date_precision: string | null
  source_name: string | null
  source_url: string | null
  page_count: number | null
  chunk_count: number
  word_count: number | null
  ocr_status: string
  publication_status: string
  verification_status: string
  is_demo: boolean
}

export const adminApi = {
  me: () => adminRequest<AdminUser>('/auth/me'),
  logout: clearTokens,
  stats: () => adminRequest<Stats>('/system/stats'),
  health: () => adminRequest<Record<string, unknown>>('/system/health'),

  /** The working list, which includes drafts the public endpoint hides. */
  documents: (params: {
    limit?: number
    offset?: number
    q?: string
    publication_status?: string
    verification_status?: string
    sort?: string
  } = {}) => {
    const query = new URLSearchParams()
    if (params.limit) query.set('limit', String(params.limit))
    if (params.offset) query.set('offset', String(params.offset))
    if (params.q) query.set('q', params.q)
    if (params.publication_status) query.set('publication_status', params.publication_status)
    if (params.verification_status) query.set('verification_status', params.verification_status)
    if (params.sort) query.set('sort', params.sort)
    const suffix = query.toString()
    return adminRequest<{ total: number; items: DocumentRow[] }>(
      `/admin/documents${suffix ? `?${suffix}` : ''}`,
    )
  },

  jobs: (status?: string) =>
    adminRequest<JobList>(`/admin/jobs${status ? `?status=${status}` : ''}`),
  retryJob: (id: string) => adminRequest<{ detail: string }>(`/admin/jobs/${id}/retry`, { method: 'POST' }),
  indexStatus: () => adminRequest<Record<string, unknown>>('/admin/jobs/index-status'),

  ocrQueue: (limit = 50) => adminRequest<{ total: number; items: OcrQueueItem[] }>(`/admin/ocr/queue?limit=${limit}`),
  reprocessOcr: (documentId: string, pageNumber: number) =>
    adminRequest<Record<string, unknown>>(
      `/admin/ocr/reprocess?document_id=${encodeURIComponent(documentId)}&page_number=${pageNumber}`,
      { method: 'POST' },
    ),
  approvePage: (identifier: string, pageNumber: number, correctedText: string | null) =>
    adminRequest<Record<string, unknown>>(
      `/documents/${encodeURIComponent(identifier)}/pages/${pageNumber}/review`,
      { method: 'POST', body: JSON.stringify({ corrected_text: correctedText }) },
    ),

  publish: (identifier: string) =>
    adminRequest<{ document_id: string; publication_status: string }>(
      `/documents/${encodeURIComponent(identifier)}/publish`,
      { method: 'POST' },
    ),
  withdraw: (identifier: string) =>
    adminRequest<{ detail: string }>(`/documents/${encodeURIComponent(identifier)}/withdraw`, { method: 'POST' }),
  reindex: (identifier: string) =>
    adminRequest<{ document_id: string; chunks: number; embedded: number; status: string }>(
      `/documents/${encodeURIComponent(identifier)}/reindex`,
      { method: 'POST' },
    ),
  versions: (identifier: string) =>
    adminRequest<Array<Record<string, unknown>>>(`/documents/${encodeURIComponent(identifier)}/versions`),
  verifyObjects: (identifier: string) =>
    adminRequest<Record<string, unknown>>(`/documents/${encodeURIComponent(identifier)}/verify`),

  pendingRelationships: (limit = 50) =>
    adminRequest<RelationshipReview[]>(`/admin/review/relationships?limit=${limit}`),
  decideRelationship: (id: string, status: 'verified' | 'rejected', note: string | null) =>
    adminRequest<{ detail: string }>(`/admin/review/relationships/${id}`, {
      method: 'POST',
      body: JSON.stringify({ status, note }),
    }),
  reviewNodes: () => adminRequest<Array<Record<string, unknown>>>('/admin/review/graph-nodes'),

  /** Returns a flat list, newest first, which is what the endpoint sends. */
  audit: (params: { limit?: number; action?: string } = {}) => {
    const query = new URLSearchParams()
    query.set('limit', String(params.limit ?? 100))
    if (params.action) query.set('action', params.action)
    return adminRequest<AuditEntry[]>(`/admin/audit?${query.toString()}`)
  },

  users: () => adminRequest<AdminUser[]>('/auth/users'),
  createUser: (body: { email: string; password: string; full_name?: string; role: string }) =>
    adminRequest<AdminUser>('/auth/users', { method: 'POST', body: JSON.stringify(body) }),
  deactivateUser: (id: string) => adminRequest<{ detail: string }>(`/auth/users/${id}/deactivate`, { method: 'POST' }),
  roles: () => adminRequest<RoleDefinition[]>('/auth/roles'),
  changePassword: (currentPassword: string, newPassword: string) =>
    adminRequest<{ detail: string }>('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
}
