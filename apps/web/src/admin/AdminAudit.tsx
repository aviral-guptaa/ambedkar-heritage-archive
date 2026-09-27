import { useState } from 'react'
import { adminApi } from '../lib/adminApi'
import { formatDate, formatNumber } from '../lib/format'
import { useLoader } from './useLoader'
import { Empty, LoadFailure, Loading, Panel } from './AdminShell'

/**
 * Who did what to the archive.
 *
 * A row with no actor is shown as such rather than blank, because a scheduled
 * or system action and a person's action mean different things when a curator
 * is asking why a record changed.
 */
export function AdminAudit() {
  const [action, setAction] = useState('')
  const entries = useLoader(() => adminApi.audit({ limit: 200, action: action || undefined }), [action])

  const actions = Array.from(
    new Set((entries.data ?? []).map((entry) => entry.action)),
  ).sort()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Audit log</h1>
        <p className="mt-1 text-sm text-stone-600">
          Every editorial action, recorded with the account that made it.
        </p>
      </div>

      <Panel
        title={entries.data ? `${formatNumber(entries.data.length)} entries` : 'Entries'}
        action={
          <select
            value={action}
            onChange={(event) => setAction(event.target.value)}
            className="rounded-md border border-stone-300 px-2 py-1.5 text-sm"
          >
            <option value="">All actions</option>
            {actions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        }
      >
        {entries.loading ? (
          <Loading />
        ) : entries.error ? (
          <LoadFailure error={entries.error} requestId={entries.requestId} />
        ) : entries.data?.length === 0 ? (
          <Empty>No entries recorded yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-stone-300 text-xs uppercase tracking-wide text-stone-500">
                  <th className="py-2 pr-3">When</th>
                  <th className="py-2 pr-3">Actor</th>
                  <th className="py-2 pr-3">Action</th>
                  <th className="py-2 pr-3">Entity</th>
                  <th className="py-2">Detail</th>
                </tr>
              </thead>
              <tbody>
                {entries.data?.map((entry) => (
                  <tr key={entry.id} className="border-b border-stone-200 align-top">
                    <td className="py-2 pr-3 whitespace-nowrap">{formatDate(entry.created_at, null)}</td>
                    <td className="py-2 pr-3">
                      {entry.actor_email ?? (
                        <span className="text-stone-500">system or scheduled</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs">{entry.action}</td>
                    <td className="py-2 pr-3 text-xs">
                      {entry.entity_type ? `${entry.entity_type} ` : ''}
                      <span className="font-mono">{entry.entity_id ?? '—'}</span>
                    </td>
                    <td className="py-2 text-xs text-stone-600">
                      {entry.detail ? JSON.stringify(entry.detail) : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
