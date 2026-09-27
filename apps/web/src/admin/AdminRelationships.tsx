import { useState } from 'react'
import { ApiError, adminApi, type RelationshipReview } from '../lib/adminApi'
import { formatNumber } from '../lib/format'
import { useLoader } from './useLoader'
import { Action, Empty, LoadFailure, Loading, Outcome, Panel } from './AdminShell'

/**
 * Confirming or rejecting relationships the extractor proposed.
 *
 * These are machine-inferred connections between people, places, documents and
 * events. Accepting one puts it in the public knowledge graph, so a rejection
 * is a normal and expected outcome — nothing here is treated as pending
 * automatically.
 */
export function AdminRelationships() {
  const pending = useLoader(() => adminApi.pendingRelationships(50))
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})

  async function decide(item: RelationshipReview, status: 'verified' | 'rejected') {
    setBusy(item.id)
    setNotice(null)
    setFailure(null)
    try {
      await adminApi.decideRelationship(item.id, status, notes[item.id] || null)
      setNotice(
        status === 'verified'
          ? `Accepted: ${describe(item)}. It is now part of the public graph.`
          : `Rejected: ${describe(item)}.`,
      )
      pending.reload()
    } catch (cause) {
      setFailure(cause instanceof ApiError ? cause.message : 'That decision could not be recorded.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Relationship review</h1>
        <p className="mt-1 text-sm text-stone-600">
          Connections the extractor proposed. Accepting one publishes it in the knowledge graph; a note is
          kept with the relationship.
        </p>
      </div>

      <Outcome notice={notice} failure={failure} />

      {pending.loading ? (
        <Loading />
      ) : pending.error ? (
        <LoadFailure error={pending.error} requestId={pending.requestId} />
      ) : (
        <Panel title={`${formatNumber(pending.data?.length ?? null)} awaiting a decision`}>
          {pending.data?.length === 0 ? (
            <Empty>Nothing is waiting. Every proposed relationship has been decided.</Empty>
          ) : (
            <ul className="space-y-4">
              {pending.data?.map((item) => (
                <li key={item.id} className="rounded-lg border border-stone-200 p-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="font-medium">
                      {item.from_label ?? item.from_id}{' '}
                      <span className="text-stone-500">{item.relation}</span> {item.to_label ?? item.to_id}
                    </p>
                    <p className="text-xs text-stone-500">
                      {item.from_type} → {item.to_type}
                      {item.confidence !== null && ` · ${Math.round(item.confidence * 100)}% confidence`}
                    </p>
                  </div>
                  <div className="mt-3 flex flex-wrap items-end gap-2">
                    <input
                      value={notes[item.id] ?? ''}
                      onChange={(event) =>
                        setNotes((current) => ({ ...current, [item.id]: event.target.value }))
                      }
                      placeholder="Note (optional)"
                      className="min-w-48 flex-1 rounded-md border border-stone-300 px-3 py-1.5 text-sm"
                    />
                    <Action disabled={busy === item.id} onClick={() => void decide(item, 'verified')}>
                      Accept
                    </Action>
                    <Action
                      disabled={busy === item.id}
                      tone="danger"
                      onClick={() => void decide(item, 'rejected')}
                    >
                      Reject
                    </Action>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}
    </div>
  )
}

function describe(item: RelationshipReview): string {
  return `${item.from_label ?? item.from_id} ${item.relation} ${item.to_label ?? item.to_id}`
}
