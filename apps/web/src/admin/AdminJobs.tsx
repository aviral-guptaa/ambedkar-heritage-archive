import { useState } from 'react'
import { ApiError, adminApi } from '../lib/adminApi'
import { formatDate, formatNumber } from '../lib/format'
import { useLoader } from './useLoader'
import { Action, Empty, LoadFailure, Loading, Outcome, Panel } from './AdminShell'

/**
 * What the background workers are doing, and what failed.
 *
 * A job that failed is shown with the error the pipeline actually raised,
 * because "failed" on its own tells a curator nothing about whether retrying
 * will help.
 */
export function AdminJobs() {
  const [status, setStatus] = useState('')
  const jobs = useLoader(() => adminApi.jobs(status || undefined), [status])
  const index = useLoader(() => adminApi.indexStatus())
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  async function retry(id: string) {
    setBusy(id)
    setNotice(null)
    setFailure(null)
    try {
      const result = await adminApi.retryJob(id)
      setNotice(result.detail)
      jobs.reload()
    } catch (cause) {
      setFailure(cause instanceof ApiError ? cause.message : 'That job could not be requeued.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Background jobs</h1>
        <p className="mt-1 text-sm text-stone-600">
          Ingestion, embedding, graph extraction and verification run here.
        </p>
      </div>

      <Outcome notice={notice} failure={failure} />

      {jobs.data && (
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="Queue depth" value={formatNumber(jobs.data.queue_depth)} />
          <Stat
            label="Workers alive"
            value={formatNumber(jobs.data.workers_alive)}
            note={
              jobs.data.workers_alive === 0
                ? 'Nothing is processing. Start the worker to make progress.'
                : undefined
            }
          />
          <Stat label="Backend" value={jobs.data.backend} />
        </div>
      )}

      {index.data && (
        <Panel title="Embedding index coverage">
          <IndexCoverage data={index.data} />
        </Panel>
      )}

      <Panel
        title={jobs.data ? `Jobs (${formatNumber(jobs.data.total)} total)` : 'Jobs'}
        action={
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="rounded-md border border-stone-300 px-2 py-1.5 text-sm"
          >
            <option value="">All states</option>
            <option value="queued">Queued</option>
            <option value="processing">Processing</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        }
      >
        {jobs.loading ? (
          <Loading />
        ) : jobs.error ? (
          <LoadFailure error={jobs.error} requestId={jobs.requestId} />
        ) : jobs.data?.items.length === 0 ? (
          <Empty>No jobs in that state.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-stone-300 text-xs uppercase tracking-wide text-stone-500">
                  <th className="py-2 pr-3">Type</th>
                  <th className="py-2 pr-3">State</th>
                  <th className="py-2 pr-3">Progress</th>
                  <th className="py-2 pr-3">Created</th>
                  <th className="py-2 pr-3">Problem</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {jobs.data?.items.map((job) => (
                  <tr key={job.id} className="border-b border-stone-200 align-top">
                    <td className="py-2 pr-3">
                      {job.job_type}
                      {job.attempts > 1 && (
                        <span className="block text-xs text-stone-500">
                          {job.attempts} attempts
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      <StateBadge state={job.status} />
                    </td>
                    <td className="py-2 pr-3">{Math.round(job.progress * 100)}%</td>
                    <td className="py-2 pr-3 whitespace-nowrap">{formatDate(job.created_at, null)}</td>
                    <td className="py-2 pr-3 text-xs text-red-800">{job.error ?? ''}</td>
                    <td className="py-2">
                      {job.status === 'failed' && (
                        <Action disabled={busy === job.id} onClick={() => void retry(job.id)}>
                          Requeue
                        </Action>
                      )}
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

function IndexCoverage({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([, value]) => typeof value === 'number' || typeof value === 'string' || typeof value === 'boolean',
  )
  if (entries.length === 0) return <Empty>No coverage figures were returned.</Empty>
  return (
    <dl className="grid gap-3 sm:grid-cols-3">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt className="text-xs uppercase tracking-wide text-stone-500">{key.replace(/_/g, ' ')}</dt>
          <dd className="text-lg font-semibold">{String(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function StateBadge({ state }: { state: string }) {
  const tones: Record<string, string> = {
    completed: 'border-emerald-300 bg-emerald-50 text-emerald-900',
    failed: 'border-red-300 bg-red-50 text-red-900',
    processing: 'border-sky-300 bg-sky-50 text-sky-900',
    running: 'border-sky-300 bg-sky-50 text-sky-900',
    queued: 'border-amber-300 bg-amber-50 text-amber-900',
    retrying: 'border-amber-300 bg-amber-50 text-amber-900',
  }
  return (
    <span className={`rounded border px-2 py-0.5 text-xs ${tones[state] ?? 'border-stone-300 text-stone-700'}`}>
      {state}
    </span>
  )
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-stone-300 bg-white p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-stone-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      {note && <p className="mt-0.5 text-xs text-amber-800">{note}</p>}
    </div>
  )
}
