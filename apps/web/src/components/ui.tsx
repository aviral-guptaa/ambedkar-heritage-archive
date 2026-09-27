import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { VerificationBadge, SourcePointer } from './Provenance'
import { formatDate, formatNumber, titleCase } from '../lib/format'
import type { DocumentSummary } from '../lib/api'

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-10 text-sm text-ink-600" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-200 border-t-indigo-500" />
      {label}…
    </div>
  )
}

/** An error, shown as an error. Never rendered as an empty result. */
export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : 'Something went wrong.'
  const requestId = (error as { requestId?: string } | null)?.requestId
  return (
    <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900" role="alert">
      <p className="font-semibold">The archive service returned an error.</p>
      <p className="mt-1">{message}</p>
      {requestId && (
        <p className="mt-1 font-mono text-xs text-red-800/80">Request id: {requestId}</p>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md border border-red-400 px-3 py-1.5 text-xs font-medium hover:bg-red-100"
        >
          Try again
        </button>
      )}
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-ink-200 bg-ink-50/60 p-8 text-center">
      <p className="font-serif text-lg text-ink-800">{title}</p>
      {children && <div className="mt-2 text-sm text-ink-600">{children}</div>}
    </div>
  )
}

/**
 * A section that is not built yet.
 *
 * The public navigation is fixed, so an unfinished section is still listed. It
 * says plainly that it is unavailable rather than showing an empty screen that
 * could be mistaken for "there is nothing here".
 */
export function NotAvailable({
  title,
  reason,
  whatItWillDo,
}: {
  title: string
  reason: string
  whatItWillDo: string
}) {
  return (
    <div className="mx-auto max-w-2xl py-10">
      <p className="text-xs font-semibold uppercase tracking-widest text-amber-700">
        Not yet available
      </p>
      <h1 className="mt-2 font-serif text-3xl text-ink-900">{title}</h1>
      <p className="mt-3 text-ink-700">{reason}</p>
      <div className="mt-6 rounded-lg border border-ink-200 bg-white p-5">
        <p className="text-sm font-semibold text-ink-800">What this section will contain</p>
        <p className="mt-1.5 text-sm leading-relaxed text-ink-700">{whatItWillDo}</p>
      </div>
    </div>
  )
}

export function DocumentCard({ document }: { document: DocumentSummary }) {
  return (
    <article className="group flex flex-col gap-2 rounded-lg border border-ink-200 bg-white p-4 transition hover:border-indigo-400 hover:shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-serif text-lg leading-snug text-ink-900">
          <Link to={`/manuscripts/${document.slug}`} className="hover:text-indigo-700">
            {document.title}
          </Link>
        </h3>
        <span className="shrink-0 rounded bg-ink-100 px-2 py-0.5 text-[0.7rem] uppercase tracking-wide text-ink-600">
          {titleCase(document.document_type)}
        </span>
      </div>
      <p className="text-sm text-ink-600">
        {document.author ?? 'Author not recorded'} ·{' '}
        {formatDate(document.document_date, document.date_precision, document.year)}
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-600">
        <VerificationBadge status={document.verification_status} compact />
        <span>{formatNumber(document.word_count)} words</span>
        {document.source_tier && <span>· {titleCase(document.source_tier)}</span>}
      </div>
    </article>
  )
}

export function CitationList({
  citations,
}: {
  citations: Array<{
    marker: number
    document_title: string
    page_number: number | null
    page_information_unavailable: boolean
    verified: boolean
    verification_status: string
    provenance_warning: string | null
    source_url: string | null
    source_reference: string | null
  }>
}) {
  if (citations.length === 0) return null
  return (
    <section aria-labelledby="citations-heading" className="rounded-lg border border-ink-200 bg-ink-50/50 p-4">
      <h3 id="citations-heading" className="font-serif text-lg text-ink-900">
        Sources cited
      </h3>
      <ol className="mt-3 space-y-3">
        {citations.map((citation) => (
          <li key={citation.marker} className="text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-ink-200 px-1.5 py-0.5 font-mono text-xs text-ink-800">
                {citation.marker}
              </span>
              <span className="font-medium text-ink-900">{citation.document_title}</span>
              <VerificationBadge
                status={citation.verification_status}
                quoteVerified={citation.verified}
                compact
              />
            </div>
            <div className="mt-1">
              <SourcePointer
                pageNumber={citation.page_number}
                unavailable={citation.page_information_unavailable}
                sourceUrl={citation.source_url}
                sourceReference={citation.source_reference}
              />
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

export function Pagination({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number
  limit: number
  total: number
  onChange: (nextOffset: number) => void
}) {
  if (total <= limit) return null
  const from = offset + 1
  const to = Math.min(offset + limit, total)
  return (
    <nav className="flex items-center justify-between gap-4 pt-2" aria-label="Pagination">
      <p className="text-sm text-ink-600">
        Showing {from}–{to} of {formatNumber(total)}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
          className="rounded-md border border-ink-300 px-3 py-1.5 text-sm disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          disabled={to >= total}
          onClick={() => onChange(offset + limit)}
          className="rounded-md border border-ink-300 px-3 py-1.5 text-sm disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </nav>
  )
}

export function NotFound() {
  return (
    <div className="mx-auto max-w-xl py-16 text-center">
      <p className="text-xs font-semibold uppercase tracking-widest text-ink-400">Not found</p>
      <h1 className="mt-2 font-serif text-3xl text-ink-900">That page is not part of the archive.</h1>
      <p className="mt-3 text-ink-600">
        The address may be mistyped, or the record may not be published. Nothing has been removed
        from the archive: unpublished records are not shown to the public at all.
      </p>
      <Link
        to="/"
        className="mt-6 inline-block rounded-md bg-indigo-900 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
      >
        Return to the home page
      </Link>
    </div>
  )
}
