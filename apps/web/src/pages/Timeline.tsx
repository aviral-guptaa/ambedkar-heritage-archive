import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type TimelineResponse } from '../lib/api'
import { VerificationBadge } from '../components/Provenance'
import { EmptyState, ErrorNotice, Spinner } from '../components/ui'
import { formatDate, titleCase } from '../lib/format'

export function Timeline() {
  const [data, setData] = useState<TimelineResponse | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [yearFrom, setYearFrom] = useState('')
  const [yearTo, setYearTo] = useState('')
  // A decade at a time: rendering every card at once is slow on the modest
  // hardware a kiosk may run on, and the archive will keep growing.
  const [visibleYears, setVisibleYears] = useState(10)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .timeline({
        limit: 2000,
        year_from: yearFrom ? Number(yearFrom) : undefined,
        year_to: yearTo ? Number(yearTo) : undefined,
      })
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
  }, [yearFrom, yearTo])

  /** Events grouped by year, for a compact year rail down the side. */
  const byYear = useMemo(() => {
    const groups = new Map<number, typeof data extends null ? never : NonNullable<typeof data>['events']>()
    for (const event of data?.events ?? []) {
      if (event.year === null) continue
      const bucket = groups.get(event.year)
      if (bucket) bucket.push(event)
      else groups.set(event.year, [event])
    }
    return [...groups.entries()].sort((a, b) => a[0] - b[0])
  }, [data])

  const undated = (data?.events ?? []).filter((event) => event.year === null)

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-3xl text-ink-900">Timeline</h1>
        <p className="mt-2 max-w-3xl text-ink-700">
          Every dated record in the archive, in order. Dates are shown at the precision the record
          itself carries — a record known only to a year is not given an invented month or day.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-ink-200 bg-white p-4 text-sm">
        <label>
          <span className="mb-1 block text-xs text-ink-600">From year</span>
          <input
            type="number"
            value={yearFrom}
            onChange={(event) => setYearFrom(event.target.value)}
            placeholder={data?.year_from ? String(data.year_from) : '1916'}
            className="w-28 rounded-md border border-ink-300 px-2 py-1.5"
          />
        </label>
        <label>
          <span className="mb-1 block text-xs text-ink-600">To year</span>
          <input
            type="number"
            value={yearTo}
            onChange={(event) => setYearTo(event.target.value)}
            placeholder={data?.year_to ? String(data.year_to) : '1956'}
            className="w-28 rounded-md border border-ink-300 px-2 py-1.5"
          />
        </label>
        {(yearFrom || yearTo) && (
          <button
            type="button"
            onClick={() => {
              setYearFrom('')
              setYearTo('')
              setVisibleYears(10)
            }}
            className="rounded-md border border-ink-300 px-3 py-1.5 text-xs hover:bg-ink-50"
          >
            Clear
          </button>
        )}
      </div>

      {loading && <Spinner label="Loading the timeline" />}
      {error != null && <ErrorNotice error={error} />}

      {data && !loading && (
        <>
          <p className="text-sm text-ink-600">
            {data.total} event{data.total === 1 ? '' : 's'}
            {data.year_from && data.year_to ? ` between ${data.year_from} and ${data.year_to}` : ''}
          </p>

          {data.years_with_no_data.length > 0 && (
            <p className="text-xs text-ink-500">
              The archive holds no record for {data.years_with_no_data.length} year
              {data.years_with_no_data.length === 1 ? '' : 's'} in this range. That is a gap in
              what has been collected, not evidence that nothing was said or written.
            </p>
          )}

          {byYear.length === 0 ? (
            <EmptyState title="No dated records in that range.">
              Widen the year range to see the rest of the archive.
            </EmptyState>
          ) : (
            <ol className="relative space-y-8 border-l-2 border-ink-200 pl-6">
              {byYear.slice(0, visibleYears).map(([year, events]) => (
                <li key={year} className="relative">
                  <h2 className="absolute -left-[1.9rem] flex h-8 w-8 items-center justify-center rounded-full border-2 border-indigo-500 bg-white font-mono text-[0.6rem] text-indigo-700">
                    {String(year).slice(2)}
                  </h2>
                  <h3 className="font-serif text-2xl text-ink-900">{year}</h3>
                  <ul className="mt-3 space-y-2">
                    {events.map((event) => (
                      <li
                        key={event.id}
                        className="rounded-lg border border-ink-200 bg-white p-4"
                      >
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <h4 className="font-serif text-lg text-ink-900">{event.title}</h4>
                          <VerificationBadge status={event.verification_status} compact />
                        </div>
                        <p className="mt-0.5 text-xs text-ink-500">
                          {formatDate(event.event_date, event.date_precision, event.year)}
                          {event.event_type ? ` · ${titleCase(event.event_type)}` : ''}
                          {event.place ? ` · ${event.place}` : ''}
                        </p>
                        {event.description && (
                          <p className="mt-2 text-sm leading-relaxed text-ink-700">
                            {event.description}
                          </p>
                        )}
                        {event.document_count > 0 && (
                          <p className="mt-2 text-xs text-indigo-700">
                            {event.document_count} record{event.document_count === 1 ? '' : 's'} in
                            the archive
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ol>
          )}

          {byYear.length > visibleYears && (
            <div className="flex flex-wrap items-center justify-center gap-3">
              <button
                type="button"
                onClick={() => setVisibleYears((count) => count + 10)}
                className="rounded-md border border-ink-300 px-4 py-2 text-sm hover:bg-ink-50"
              >
                Show later years ({byYear.length - visibleYears} remaining)
              </button>
              <button
                type="button"
                onClick={() => setVisibleYears(byYear.length)}
                className="rounded-md px-3 py-2 text-sm text-indigo-700 underline-offset-2 hover:underline"
              >
                Show all
              </button>
            </div>
          )}

          {undated.length > 0 && (
            <section>
              <h2 className="font-serif text-xl text-ink-900">Undated records</h2>
              <p className="mt-1 text-sm text-ink-600">
                These records carry no reliable date, so they are kept out of the sequence rather
                than guessed into a year.
              </p>
              <ul className="mt-3 space-y-2">
                {undated.slice(0, 25).map((event) => (
                  <li key={event.id} className="rounded-lg border border-ink-200 bg-white p-3 text-sm">
                    {event.title}
                  </li>
                ))}
              </ul>
              {undated.length > 25 && (
                <p className="mt-2 text-xs text-ink-500">
                  and {undated.length - 25} more undated records, listed{' '}
                  <Link to="/manuscripts" className="underline underline-offset-2">
                    with the manuscripts
                  </Link>
                  .
                </p>
              )}
            </section>
          )}

          <p className="text-xs text-ink-500">
            Looking for a specific record?{' '}
            <Link to="/manuscripts" className="underline underline-offset-2">
              Browse the manuscripts
            </Link>
            .
          </p>
        </>
      )}
    </div>
  )
}
