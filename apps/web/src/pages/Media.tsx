import { useEffect, useState } from 'react'
import { api, type MediaResponse } from '../lib/api'
import { VerificationBadge } from '../components/Provenance'
import { EmptyState, ErrorNotice, Spinner } from '../components/ui'
import { titleCase } from '../lib/format'

const KINDS = ['', 'image', 'audio', 'video']

export function Media() {
  const [data, setData] = useState<MediaResponse | null>(null)
  const [kind, setKind] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .media({ kind: kind || undefined, limit: 60 })
      .then((value) => {
        if (!cancelled) setData(value)
      })
      .catch((caught) => {
        if (!cancelled) setError(caught)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [kind])

  const items = data?.items ?? []

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-3xl text-ink-900">Media</h1>
        <p className="mt-2 max-w-3xl text-ink-700">
          Photographs, audio and film held alongside the records. Each item is shown with the record
          it belongs to, because a photograph without its provenance is just an assertion.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-ink-200 bg-white p-4 text-sm">
        <span className="text-xs text-ink-600">Filter by type</span>
        {KINDS.map((value) => (
          <button
            key={value || 'all'}
            type="button"
            onClick={() => setKind(value)}
            className={`rounded-full border px-3 py-1 text-xs ${
              kind === value
                ? 'border-indigo-700 bg-indigo-900 text-white'
                : 'border-ink-300 hover:bg-ink-50'
            }`}
          >
            {value ? titleCase(value) : 'All'}
          </button>
        ))}
      </div>

      {loading && <Spinner label="Loading media" />}
      {error != null && <ErrorNotice error={error} />}

      {data && !loading && (
        <>
          <p className="text-sm text-ink-600">
            {data.total} item{data.total === 1 ? '' : 's'}
          </p>
          {items.length === 0 ? (
            <EmptyState title="No media items have been catalogued yet.">
              The archive currently holds text records only. Media is added as it is digitised and
              its provenance recorded.
            </EmptyState>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((item) => (
                <li key={item.id} className="overflow-hidden rounded-lg border border-ink-200 bg-white">
                  {item.thumbnail_url ? (
                    <img
                      src={item.thumbnail_url}
                      alt={item.title}
                      loading="lazy"
                      className="h-40 w-full object-cover"
                    />
                  ) : (
                    <div className="flex h-40 items-center justify-center bg-ink-100 text-xs uppercase tracking-widest text-ink-400">
                      {titleCase(item.kind)}
                    </div>
                  )}
                  <div className="p-3">
                    <h2 className="font-medium text-ink-900">{item.title}</h2>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <VerificationBadge
                        status={item.verification_status ?? 'unverified_secondary'}
                        quoteVerified={false}
                        compact
                      />
                    </div>
                    {item.url && (
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="mt-2 inline-block text-xs text-indigo-700 underline underline-offset-2"
                      >
                        Open
                      </a>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
