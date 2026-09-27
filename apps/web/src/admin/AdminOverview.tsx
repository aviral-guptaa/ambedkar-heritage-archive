import { Link } from 'react-router-dom'
import { formatNumber } from '../lib/format'
import { adminApi } from '../lib/adminApi'
import { useAdminSession } from './AdminSession'
import { useLoader } from './useLoader'
import { LoadFailure, Loading, Panel } from './AdminShell'

/**
 * The overview a curator opens first: what state is the archive in, and what is
 * waiting for a person. The honest-archive guarantee depends on the second half
 * of that — an archive with a growing "unverified" pile is working correctly.
 */
export function AdminOverview() {
  const { user, role } = useAdminSession()
  const stats = useLoader(() => adminApi.stats())
  const jobs = useLoader(() => adminApi.jobs('failed'))
  const ocr = useLoader(() => adminApi.ocrQueue(1))
  const relationships = useLoader(() => adminApi.pendingRelationships(1))

  const unindexed =
    stats.data && !stats.loading ? stats.data.chunks - stats.data.embedded_chunks : null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Archive overview</h1>
        <p className="mt-1 text-sm text-stone-600">
          Signed in as {user?.email} with the {role} role.
        </p>
      </div>

      {stats.error && <LoadFailure error={stats.error} requestId={stats.requestId} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="Documents" value={formatNumber(stats.data?.documents_total)} />
        <Tile label="Published" value={formatNumber(stats.data?.documents_published)} />
        <Tile label="Manuscripts" value={formatNumber(stats.data?.manuscripts)} />
        <Tile label="Pages" value={formatNumber(stats.data?.pages)} />
        <Tile label="Chunks" value={formatNumber(stats.data?.chunks)} />
        <Tile
          label="Chunks embedded"
          value={formatNumber(stats.data?.embedded_chunks)}
          note={
            unindexed === null
              ? undefined
              : unindexed > 0
                ? `${formatNumber(unindexed)} waiting for an embedding pass`
                : 'All embedded'
          }
        />
        <Tile label="Graph nodes" value={formatNumber(stats.data?.graph_nodes)} />
        <Tile
          label="Stored data"
          value={`${formatNumber(Math.round((stats.data?.stored_bytes ?? 0) / 1024 / 1024))} MB`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Failed jobs">
          {jobs.loading ? (
            <Loading />
          ) : jobs.error ? (
            <LoadFailure error={jobs.error} requestId={jobs.requestId} />
          ) : jobs.data && jobs.data.items.length > 0 ? (
            <ul className="space-y-2 text-sm">
              {jobs.data.items.slice(0, 5).map((job) => (
                <li key={job.id} className="flex items-start justify-between gap-3">
                  <span>
                    <span className="font-medium">{job.job_type}</span>
                    {job.error && <span className="block text-xs text-red-700">{job.error}</span>}
                  </span>
                  <Link className="text-xs text-stone-600 underline" to="/admin/jobs">
                    Review
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-stone-600">No failed jobs. Queue depth {jobs.data?.queue_depth}.</p>
          )}
        </Panel>

        <Panel title="Pages awaiting OCR review">
          {ocr.loading ? (
            <Loading />
          ) : ocr.error ? (
            <LoadFailure error={ocr.error} requestId={ocr.requestId} />
          ) : (
            <p className="text-sm">
              <span className="text-2xl font-semibold">{formatNumber(ocr.data?.total)}</span> pages
              need a person to read them.
              <Link className="mt-2 block text-xs text-stone-600 underline" to="/admin/ocr">
                Open the review queue
              </Link>
            </p>
          )}
        </Panel>

        <Panel title="Relationships to confirm">
          {relationships.loading ? (
            <Loading />
          ) : relationships.error ? (
            <LoadFailure error={relationships.error} requestId={relationships.requestId} />
          ) : (
            <p className="text-sm">
              <span className="text-2xl font-semibold">
                {formatNumber(relationships.data?.length ?? null)}
              </span>{' '}
              awaiting a decision.
              <Link className="mt-2 block text-xs text-stone-600 underline" to="/admin/relationships">
                Review candidates
              </Link>
            </p>
          )}
        </Panel>
      </div>
    </div>
  )
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-stone-300 bg-white p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-stone-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      {note && <p className="mt-0.5 text-xs text-stone-600">{note}</p>}
    </div>
  )
}
