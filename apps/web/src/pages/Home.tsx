import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ArchiveStats, type DocumentSummary, type TimelineEvent } from '../lib/api'
import { CorpusNotice, VerificationBadge } from '../components/Provenance'
import { DocumentCard, ErrorNotice, Spinner } from '../components/ui'
import { formatDate, formatNumber } from '../lib/format'

export function Home() {
  const [stats, setStats] = useState<ArchiveStats | null>(null)
  const [recent, setRecent] = useState<DocumentSummary[]>([])
  const [timeline, setTimeline] = useState<TimelineEvent[]>([])
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([
      api.stats(),
      api.documents({ limit: 4, year_from: 1940 }),
      api.timeline({ limit: 6 }),
    ])
      .then(([s, docs, t]) => {
        setStats(s)
        setRecent(docs.items)
        setTimeline(t.events)
      })
      .catch(setError)
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  return (
    <div className="space-y-10">
      <section className="rounded-xl border border-ink-200 bg-white p-6 md:p-10">
        <p className="text-xs font-semibold uppercase tracking-widest text-saffron-700">
          An evidence-grounded digital heritage archive
        </p>
        <h1 className="mt-3 font-serif text-3xl leading-tight text-ink-900 md:text-4xl">
          Read what Dr. B. R. Ambedkar said — and see exactly how much the archive can vouch for.
        </h1>
        <p className="mt-4 max-w-3xl text-ink-700">
          {stats
            ? `${formatNumber(stats.documents_published)} records, ${formatNumber(
                stats.chunks,
              )} indexed passages and ${formatNumber(stats.graph_nodes)} knowledge-graph nodes, `
            : 'Records, indexed passages and a knowledge graph, '}
          built from speeches and writings held by Columbia University, the Government of India and
          similar public archives. Every passage carries its source, and every passage says whether
          anyone has checked the wording against the original.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            to="/explore"
            className="rounded-md bg-indigo-900 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            Search the archive
          </Link>
          <Link
            to="/ai-research"
            className="rounded-md border border-indigo-900 px-4 py-2 text-sm font-medium text-indigo-900 hover:bg-indigo-50"
          >
            Ask a question
          </Link>
        </div>
      </section>

      <CorpusNotice />

      {error != null && <ErrorNotice error={error} onRetry={load} />}

      {loading && <Spinner label="Loading the archive" />}

      {!loading && stats && (
        <section aria-label="Archive at a glance">
          <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              { label: 'Published records', value: stats.documents_published },
              { label: 'Indexed passages', value: stats.chunks },
              { label: 'Timeline events', value: stats.events },
              { label: 'Graph nodes', value: stats.graph_nodes },
            ].map((item) => (
              <div key={item.label} className="rounded-lg border border-ink-200 bg-white p-4">
                <dt className="text-xs uppercase tracking-wide text-ink-500">{item.label}</dt>
                <dd className="mt-1 font-serif text-2xl text-ink-900">
                  {formatNumber(item.value)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {!loading && recent.length > 0 && (
        <section aria-labelledby="recent-heading">
          <div className="flex items-baseline justify-between">
            <h2 id="recent-heading" className="font-serif text-2xl text-ink-900">
              From the late years
            </h2>
            <Link to="/manuscripts" className="text-sm text-indigo-700 underline-offset-2 hover:underline">
              All manuscripts
            </Link>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {recent.map((document) => (
              <DocumentCard key={document.id} document={document} />
            ))}
          </div>
        </section>
      )}

      {!loading && timeline.length > 0 && (
        <section aria-labelledby="timeline-heading">
          <div className="flex items-baseline justify-between">
            <h2 id="timeline-heading" className="font-serif text-2xl text-ink-900">
              On the timeline
            </h2>
            <Link to="/timeline" className="text-sm text-indigo-700 underline-offset-2 hover:underline">
              Full timeline
            </Link>
          </div>
          <ol className="mt-4 space-y-2">
            {timeline.map((event) => (
              <li
                key={event.id}
                className="flex flex-col gap-1 rounded-lg border border-ink-200 bg-white px-4 py-3 sm:flex-row sm:items-baseline sm:gap-4"
              >
                <span className="w-32 shrink-0 font-mono text-xs text-ink-600">
                  {formatDate(event.event_date, event.date_precision, event.year)}
                </span>
                <span className="flex-1 text-ink-900">{event.title}</span>
                <VerificationBadge status="unverified_secondary" compact />
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  )
}
