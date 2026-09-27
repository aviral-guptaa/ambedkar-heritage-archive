import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type DocumentSummary, type Facets, type Page } from '../lib/api'
import { DocumentCard, EmptyState, ErrorNotice, Pagination, Spinner } from '../components/ui'

const PAGE_SIZE = 12

export function Manuscripts() {
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState<Page<DocumentSummary> | null>(null)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)

  const q = params.get('q') ?? ''
  const type = params.get('type') ?? ''
  const yearFrom = params.get('from') ?? ''
  const yearTo = params.get('to') ?? ''

  const load = useCallback(
    (nextOffset: number) => {
      setLoading(true)
      setError(null)
      api
        .documents({
          limit: PAGE_SIZE,
          offset: nextOffset,
          q: q || undefined,
          document_type: type || undefined,
          year_from: yearFrom ? Number(yearFrom) : undefined,
          year_to: yearTo ? Number(yearTo) : undefined,
        })
        .then((value) => {
          setPage(value)
          setOffset(nextOffset)
        })
        .catch(setError)
        .finally(() => setLoading(false))
    },
    [q, type, yearFrom, yearTo],
  )

  useEffect(() => {
    load(0)
  }, [load])
  useEffect(() => {
    api.facets().then(setFacets).catch(() => setFacets(null))
  }, [])

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-3xl text-ink-900">Manuscripts</h1>
        <p className="mt-2 max-w-3xl text-ink-700">
          Every published record in the archive: speeches, papers, addresses and writings, with the
          source each one cites. Open a record to read the stored text and its provenance.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-ink-200 bg-white p-4 text-sm">
        <label>
          <span className="mb-1 block text-xs text-ink-600">Search titles and summaries</span>
          <input
            type="search"
            defaultValue={q}
            onChange={(event) => setFilter('q', event.target.value)}
            placeholder="e.g. constitution"
            className="w-64 rounded-md border border-ink-300 px-2 py-1.5"
          />
        </label>
        <label>
          <span className="mb-1 block text-xs text-ink-600">Type</span>
          <select
            value={type}
            onChange={(event) => setFilter('type', event.target.value)}
            className="rounded-md border border-ink-300 px-2 py-1.5"
          >
            <option value="">All</option>
            {facets?.document_types.map((facet) => (
              <option key={facet.value} value={facet.value}>
                {facet.value} ({facet.count})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="mb-1 block text-xs text-ink-600">From</span>
          <input
            type="number"
            defaultValue={yearFrom}
            onChange={(event) => setFilter('from', event.target.value)}
            className="w-24 rounded-md border border-ink-300 px-2 py-1.5"
          />
        </label>
        <label>
          <span className="mb-1 block text-xs text-ink-600">To</span>
          <input
            type="number"
            defaultValue={yearTo}
            onChange={(event) => setFilter('to', event.target.value)}
            className="w-24 rounded-md border border-ink-300 px-2 py-1.5"
          />
        </label>
        {(q || type || yearFrom || yearTo) && (
          <button
            type="button"
            onClick={() => setParams(new URLSearchParams(), { replace: true })}
            className="rounded-md border border-ink-300 px-3 py-1.5 text-xs hover:bg-ink-50"
          >
            Clear
          </button>
        )}
      </div>

      {loading && <Spinner label="Loading manuscripts" />}
      {error != null && <ErrorNotice error={error} onRetry={() => load(offset)} />}

      {page && !loading && (
        <>
          <p className="text-sm text-ink-600">
            {page.total} record{page.total === 1 ? '' : 's'}
            {q && (
              <>
                {' '}
                matching <span className="font-medium">{q}</span>
              </>
            )}
          </p>
          {page.items.length === 0 ? (
            <EmptyState title="No records matched those filters.">
              Try clearing the year range, or a different search term.
            </EmptyState>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {page.items.map((document) => (
                <DocumentCard key={document.id} document={document} />
              ))}
            </div>
          )}
          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            total={page.total}
            onChange={(next) => {
              load(next)
              window.scrollTo({ top: 0 })
            }}
          />
        </>
      )}
    </div>
  )
}
