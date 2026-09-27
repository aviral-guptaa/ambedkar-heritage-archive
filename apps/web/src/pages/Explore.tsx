import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, type Facets, type SearchHit, type SearchResponse } from '../lib/api'
import { SourcePointer, VerificationBadge, ProvenanceWarning } from '../components/Provenance'
import { EmptyState, ErrorNotice, Pagination, Spinner } from '../components/ui'

const PAGE_SIZE = 10

const SUGGESTIONS = [
  'untouchability',
  'caste and endogamy',
  'women and education',
  'constitution of India',
  'Dalit movement',
]

export function Explore() {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [type, setType] = useState('')
  const [yearFrom, setYearFrom] = useState('')
  const [yearTo, setYearTo] = useState('')
  const [response, setResponse] = useState<SearchResponse | null>(null)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.facets().then(setFacets).catch(() => setFacets(null))
  }, [])

  const run = useCallback(
    (nextOffset = 0, nextQuery = submitted) => {
      if (!nextQuery.trim()) {
        setResponse(null)
        return
      }
      setLoading(true)
      setError(null)
      api
        .search({
          query: nextQuery.trim(),
          limit: PAGE_SIZE,
          offset: nextOffset,
          document_type: type || undefined,
          year_from: yearFrom ? Number(yearFrom) : undefined,
          year_to: yearTo ? Number(yearTo) : undefined,
        })
        .then((value) => {
          setResponse(value)
          setOffset(nextOffset)
        })
        .catch(setError)
        .finally(() => setLoading(false))
    },
    [submitted, type, yearFrom, yearTo],
  )

  // Re-run when a filter changes, but only once a search has been made, so the
  // page does not fire a request for every keystroke in an empty box.
  useEffect(() => {
    if (submitted) run(offset, submitted)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, yearFrom, yearTo])

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    setSubmitted(query.trim())
    run(0, query.trim())
  }

  const results: SearchHit[] = response?.results ?? []
  const unverifiedCount = results.filter((hit) => !hit.quote_verified).length

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-3xl text-ink-900">Explore the archive</h1>
        <p className="mt-2 max-w-3xl text-ink-700">
          Search every indexed passage. Results are ranked by meaning and wording; each one shows
          which record it came from and whether anyone has checked that record against the original.
        </p>
      </header>

      <form onSubmit={onSubmit} className="space-y-3 rounded-lg border border-ink-200 bg-white p-4">
        <div className="flex flex-col gap-3 sm:flex-row">
          <label className="flex-1">
            <span className="sr-only">Search the archive</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="e.g. endogamy, untouchability, women and education"
              className="w-full rounded-md border border-ink-300 px-3 py-2 font-serif text-base focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </label>
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="rounded-md bg-indigo-900 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
          >
            Search
          </button>
        </div>
        <div className="flex flex-wrap items-end gap-3 text-sm">
          <label>
            <span className="mb-1 block text-xs text-ink-600">Document type</span>
            <select
              value={type}
              onChange={(event) => setType(event.target.value)}
              className="rounded-md border border-ink-300 px-2 py-1.5"
            >
              <option value="">All types</option>
              {facets?.document_types.map((facet) => (
                <option key={facet.value} value={facet.value}>
                  {facet.value} ({facet.count})
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-xs text-ink-600">From year</span>
            <input
              type="number"
              value={yearFrom}
              onChange={(event) => setYearFrom(event.target.value)}
              placeholder="1916"
              className="w-28 rounded-md border border-ink-300 px-2 py-1.5"
            />
          </label>
          <label>
            <span className="mb-1 block text-xs text-ink-600">To year</span>
            <input
              type="number"
              value={yearTo}
              onChange={(event) => setYearTo(event.target.value)}
              placeholder="1956"
              className="w-28 rounded-md border border-ink-300 px-2 py-1.5"
            />
          </label>
        </div>
      </form>

      {!submitted && !response && (
        <div className="space-y-3">
          <p className="text-sm text-ink-600">Try one of these:</p>
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => {
                  setQuery(suggestion)
                  setSubmitted(suggestion)
                  run(0, suggestion)
                }}
                className="rounded-full border border-ink-300 px-3 py-1 text-sm hover:border-indigo-500 hover:bg-indigo-50"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      )}

      {loading && <Spinner label="Searching" />}
      {error != null && <ErrorNotice error={error} onRetry={() => run(offset)} />}

      {response && !loading && (
        <section className="space-y-4" aria-label="Search results">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm text-ink-700">
              {response.total} passage{response.total === 1 ? '' : 's'} matching{' '}
              <span className="font-medium">{response.query}</span>
            </p>
            {unverifiedCount > 0 && (
              <p className="text-xs text-amber-800">
                {unverifiedCount} of {results.length} shown are unverified secondary text
              </p>
            )}
          </div>

          {results.length === 0 ? (
            <EmptyState title="No passages matched that search.">
              Try a broader term, or remove the year and type filters. The archive holds{' '}
              {formatCount(response.total)} records in total.
            </EmptyState>
          ) : (
            <ol className="space-y-3">
              {results.map((hit) => (
                <li key={hit.chunk_id} className="rounded-lg border border-ink-200 bg-white p-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h3 className="font-serif text-lg text-ink-900">
                      {hit.slug ? (
                        <Link
                          to={`/manuscripts/${hit.slug}`}
                          className="hover:text-indigo-700"
                        >
                          {hit.document_title}
                        </Link>
                      ) : (
                        hit.document_title
                      )}
                    </h3>
                    <VerificationBadge
                      status={hit.verification_status}
                      quoteVerified={hit.quote_verified}
                    />
                  </div>
                  {hit.section && (
                    <p className="mt-0.5 text-xs text-ink-500">Section: {hit.section}</p>
                  )}
                  <p className="mt-2 whitespace-pre-wrap font-serif leading-relaxed text-ink-800">
                    {hit.snippet.text}
                  </p>
                  {hit.quote_verified === false && (
                    <div className="mt-3">
                      <ProvenanceWarning>
                        Shown as stored text, not as a quotation: this record has not been checked
                        against the original.
                      </ProvenanceWarning>
                    </div>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <SourcePointer
                      pageNumber={hit.page_number}
                      unavailable={hit.page_information_unavailable}
                      sourceUrl={hit.source_url}
                      sourceReference={hit.source_reference}
                      sourceName={hit.source_name}
                    />
                    <span className="text-xs text-ink-400">relevance {hit.score.toFixed(3)}</span>
                  </div>
                </li>
              ))}
            </ol>
          )}

          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            total={response.total}
            onChange={(next) => run(next)}
          />
        </section>
      )}
    </div>
  )
}

function formatCount(value: number): string {
  return new Intl.NumberFormat('en-IN').format(value)
}
