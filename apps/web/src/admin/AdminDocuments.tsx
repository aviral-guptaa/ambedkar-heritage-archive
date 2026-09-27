import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, adminApi, type DocumentRow } from '../lib/adminApi'
import { formatDate, formatNumber, titleCase } from '../lib/format'
import { VerificationBadge } from '../components/Provenance'
import { useAdminSession } from './AdminSession'
import { useLoader } from './useLoader'
import { Action, LoadFailure, Loading, Panel } from './AdminShell'

/**
 * The working list a curator edits from: publish, withdraw, reindex, and check
 * that the preserved objects still hash correctly.
 *
 * Each action states what it will do before it does it, and a refusal from the
 * server is shown rather than swallowed — being told "not permitted" or "this
 * document has no indexed text" is the most useful thing that can happen here.
 */
export function AdminDocuments() {
  const { can } = useAdminSession()
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const documents = useLoader(
    () =>
      adminApi.documents({
        limit: 25,
        q: query || undefined,
        publication_status: status || undefined,
      }),
    [query, status],
  )

  async function act(doc: DocumentRow, action: DocumentAction) {
    if (action === 'withdraw' && !window.confirm(`Withdraw "${doc.title}" from the public archive?`)) {
      return
    }
    setBusy(`${doc.id}:${action}`)
    setNotice(null)
    setFailure(null)
    try {
      let message: string
      if (action === 'publish') {
        const result = await adminApi.publish(doc.id)
        message = `published as ${result.publication_status}`
      } else if (action === 'withdraw') {
        await adminApi.withdraw(doc.id)
        message = 'withdrawn from the public archive'
      } else if (action === 'reindex') {
        const result = await adminApi.reindex(doc.id)
        message = `reindexed — ${result.chunks} chunks, ${result.embedded} embedded`
      } else {
        const result = await adminApi.verifyObjects(doc.id)
        message = `objects checked — ${summariseIntegrity(result)}`
      }
      setNotice(`${doc.title}: ${message}.`)
      documents.reload()
    } catch (cause) {
      setFailure(cause instanceof ApiError ? cause.message : 'The archive refused that action.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Documents</h1>
        <p className="mt-1 text-sm text-stone-600">
          Every action here is recorded in the audit log against your account.
        </p>
      </div>

      <Panel
        title={documents.data ? `Records (${formatNumber(documents.data.total)} match)` : 'Records'}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Title, speaker, venue or ID"
              className="rounded-md border border-stone-300 px-3 py-1.5 text-sm"
            />
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="rounded-md border border-stone-300 px-2 py-1.5 text-sm"
            >
              <option value="">Any status</option>
              <option value="published">Published</option>
              <option value="draft">Draft</option>
              <option value="in_review">In review</option>
            </select>
          </div>
        }
      >
        {notice && (
          <p
            role="status"
            className="mb-3 rounded-md border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-900"
          >
            {notice}
          </p>
        )}
        {failure && (
          <p
            role="alert"
            className="mb-3 rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-900"
          >
            {failure}
          </p>
        )}

        {documents.loading ? (
          <Loading />
        ) : documents.error ? (
          <LoadFailure error={documents.error} requestId={documents.requestId} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-stone-300 text-xs uppercase tracking-wide text-stone-500">
                  <th className="py-2 pr-3">Title</th>
                  <th className="py-2 pr-3">Date</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Verification</th>
                  <th className="py-2 pr-3">Chunks</th>
                  <th className="py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {documents.data?.items.map((doc) => (
                  <tr key={doc.id} className="border-b border-stone-200 align-top">
                    <td className="py-2 pr-3">
                      <Link className="font-medium underline" to={`/manuscripts/${doc.slug}`}>
                        {doc.title}
                      </Link>
                      <span className="block font-mono text-xs text-stone-500">{doc.id}</span>
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      {formatDate(doc.document_date, doc.date_precision, doc.year)}
                    </td>
                    <td className="py-2 pr-3">{titleCase(doc.publication_status)}</td>
                    <td className="py-2 pr-3">
                      <VerificationBadge status={doc.verification_status} compact />
                    </td>
                    <td className="py-2 pr-3">{formatNumber(doc.chunk_count)}</td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-1.5">
                        {can('document:publish') && doc.publication_status !== 'published' && (
                          <Action
                            disabled={busy === `${doc.id}:publish`}
                            onClick={() => void act(doc, 'publish')}
                          >
                            Publish
                          </Action>
                        )}
                        {can('document:publish') && doc.publication_status === 'published' && (
                          <Action
                            disabled={busy === `${doc.id}:withdraw`}
                            onClick={() => void act(doc, 'withdraw')}
                          >
                            Withdraw
                          </Action>
                        )}
                        {can('document:write') && (
                          <Action
                            disabled={busy === `${doc.id}:reindex`}
                            onClick={() => void act(doc, 'reindex')}
                          >
                            Reindex
                          </Action>
                        )}
                        <Action
                          disabled={busy === `${doc.id}:verify`}
                          onClick={() => void act(doc, 'verify')}
                        >
                          Check objects
                        </Action>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {documents.data?.items.length === 0 && (
              <p className="text-sm text-stone-600">No records match that search.</p>
            )}
          </div>
        )}
      </Panel>
    </div>
  )
}

type DocumentAction = 'publish' | 'withdraw' | 'reindex' | 'verify'

/** Read the integrity check's result without echoing an opaque blob. */
function summariseIntegrity(result: Record<string, unknown>): string {
  const entries = Object.entries(result).filter(
    ([, value]) => typeof value === 'boolean' || typeof value === 'number' || typeof value === 'string',
  )
  if (entries.length === 0) return 'no details returned'
  return entries.map(([key, value]) => `${key}: ${value}`).join(', ')
}
