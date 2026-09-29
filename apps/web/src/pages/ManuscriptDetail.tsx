import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type DocumentDetail, type GroundedSummary, type DocumentText } from '../lib/api'
import { UnderstandInThirtySeconds } from '../components/UnderstandIn30Seconds'
import {
  PAGE_UNAVAILABLE,
  ProvenanceWarning,
  SourcePointer,
  VerificationBadge,
} from '../components/Provenance'
import { ErrorNotice, Spinner } from '../components/ui'
import { formatDate, formatNumber, titleCase } from '../lib/format'

export function ManuscriptDetail() {
  const { slug = '' } = useParams()
  const [document, setDocument] = useState<DocumentDetail | null>(null)
  const [text, setText] = useState<DocumentText | null>(null)
  const [summary, setSummary] = useState<GroundedSummary | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setDocument(null)
    setText(null)
    setSummary(null)
    Promise.all([
      api.document(slug),
      api.documentText(slug).catch(() => null),
      // A record too thin to summarise returns an explained "unavailable"
      // result rather than an error, so it is not caught here.
      api.documentSummary(slug).catch(() => null),
    ])
      .then(([detail, textValue, summaryValue]) => {
        if (cancelled) return
        setDocument(detail)
        setText(textValue)
        setSummary(summaryValue)
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
  }, [slug])

  if (loading) return <Spinner label="Loading record" />
  if (error != null) {
    return (
      <div className="space-y-4">
        <ErrorNotice error={error} />
        <Link to="/manuscripts" className="text-sm text-indigo-700 underline-offset-2 hover:underline">
          ← Back to manuscripts
        </Link>
      </div>
    )
  }
  if (!document) return null

  const unverified = document.verification_status !== 'verified_primary'

  return (
    <article className="space-y-8">
      <header className="space-y-3">
        <Link to="/manuscripts" className="text-sm text-indigo-700 underline-offset-2 hover:underline">
          ← All manuscripts
        </Link>
        <h1 className="font-serif text-3xl leading-tight text-ink-900 md:text-4xl">
          {document.title}
        </h1>
        <p className="text-ink-600">
          {document.author ?? 'Author not recorded'} ·{' '}
          {formatDate(document.document_date, document.date_precision, document.year)}
          {document.location ? ` · ${document.location}` : ''}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <VerificationBadge status={document.verification_status} />
          <span className="rounded bg-ink-100 px-2 py-0.5 text-xs uppercase tracking-wide text-ink-600">
            {titleCase(document.document_type)}
          </span>
          {document.language && (
            <span className="rounded bg-ink-100 px-2 py-0.5 text-xs uppercase tracking-wide text-ink-600">
              {document.language}
            </span>
          )}
        </div>
      </header>

      {unverified && (
        <ProvenanceWarning>
          This record is unverified secondary text: it was imported from a third-party dataset and
          has not been compared with the archival original. It is reproduced for reading and
          searching. Do not quote it as Dr. Ambedkar&apos;s words without checking{' '}
          {document.source_url ? 'the source it cites' : 'the original yourself'}.
        </ProvenanceWarning>
      )}

      {summary && (
        <UnderstandInThirtySeconds
          documentId={document.id}
          documentTitle={document.title}
          documentType={document.document_type}
          summary={summary}
          hasFullText={Boolean(text && text.parts.length > 0)}
        />
      )}

      {document.summary && (
        <section aria-labelledby="summary-heading">
          <h2 id="summary-heading" className="font-serif text-xl text-ink-900">
            Summary
          </h2>
          <p className="mt-2 leading-relaxed text-ink-700">{document.summary}</p>
          {unverified && (
            <p className="mt-1 text-xs text-amber-800">
              This summary was written by the dataset, not by the archive.
            </p>
          )}
        </section>
      )}

      <section id="stored-text" aria-labelledby="text-heading" className="space-y-3">
        <h2 id="text-heading" className="font-serif text-xl text-ink-900">
          Stored text
        </h2>
        {!text || text.parts.length === 0 ? (
          <p className="text-sm text-ink-600">
            No text has been indexed for this record yet.
          </p>
        ) : (
          <>
            {text.provenance_warning && <ProvenanceWarning>{text.provenance_warning}</ProvenanceWarning>}
            {/*
              The text is shown as stored, in the archive's own words about its own
              contents, and never wrapped in quotation marks: a reader must not be
              able to mistake a stored summary for a transcription.
            */}
            <div className="space-y-6 rounded-lg border border-ink-200 bg-white p-5">
              {text.parts.map((part) => (
                <section key={part.index}>
                  {part.section && (
                    <h3 className="font-serif text-base text-ink-800">{part.section}</h3>
                  )}
                  <p className="mt-1 text-xs text-ink-500">
                    {part.page_number != null ? `Page ${part.page_number}` : PAGE_UNAVAILABLE}
                  </p>
                  <p className="mt-2 whitespace-pre-wrap font-serif text-[1.02rem] leading-[1.8] text-ink-900">
                    {part.text}
                  </p>
                </section>
              ))}
            </div>
          </>
        )}
      </section>

      <section aria-labelledby="provenance-heading" className="grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-ink-200 bg-white p-4">
          <h2 id="provenance-heading" className="font-serif text-lg text-ink-900">
            Source
          </h2>
          <dl className="mt-2 space-y-1.5 text-sm">
            <div>
              <dt className="text-xs uppercase tracking-wide text-ink-500">Named source</dt>
              <dd className="text-ink-800">{document.source_name ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-ink-500">Source tier</dt>
              <dd className="text-ink-800">{titleCase(document.source_tier)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-ink-500">Reference</dt>
              <dd className="font-mono text-xs text-ink-800">
                {document.source_reference ?? 'Not recorded'}
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-ink-500">Rights</dt>
              <dd className="text-ink-800">{titleCase(document.rights)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-ink-500">Link</dt>
              <dd>
                {document.source_url ? (
                  <a
                    className="text-indigo-700 underline underline-offset-2"
                    href={document.source_url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    Open the cited source
                  </a>
                ) : (
                  <span className="italic text-ink-500">No source link recorded</span>
                )}
              </dd>
            </div>
          </dl>
          {document.source_url && (
            <p className="mt-3">
              <SourcePointer
                pageNumber={document.page_count > 0 ? 1 : null}
                unavailable={document.page_count === 0}
                sourceUrl={document.source_url}
              />
            </p>
          )}
        </div>

        <div className="rounded-lg border border-ink-200 bg-white p-4">
          <h2 className="font-serif text-lg text-ink-900">Record</h2>
          <dl className="mt-2 space-y-1.5 text-sm">
            {[
              ['Words', formatNumber(document.word_count)],
              ['Indexed passages', formatNumber(document.chunk_count)],
              ['Pages with images', document.page_count > 0 ? formatNumber(document.page_count) : 'none'],
              ['Processing', titleCase(document.processing_state)],
              [
                'Added',
                document.created_at
                  ? formatDate(document.created_at.slice(0, 10), 'day', null)
                  : 'not recorded',
              ],
            ].map(([label, value]) => (
              <div key={label} className="flex justify-between gap-4">
                <dt className="text-ink-600">{label}</dt>
                <dd className="text-right text-ink-800">{value}</dd>
              </div>
            ))}
          </dl>
          {document.checksum_sha256 && (
            <p className="mt-3 break-all font-mono text-[0.65rem] text-ink-400">
              sha256 {document.checksum_sha256}
            </p>
          )}
        </div>
      </section>

      {document.editorial_note && (
        <section className="rounded-lg border border-amber-300 bg-amber-50/70 p-4">
          <h2 className="font-serif text-lg text-amber-950">Archivist&apos;s note</h2>
          <p className="mt-2 text-sm leading-relaxed text-amber-950">{document.editorial_note}</p>
        </section>
      )}

      {document.provenance && (
        <section>
          <h2 className="font-serif text-lg text-ink-900">How this record entered the archive</h2>
          <p className="mt-2 text-sm leading-relaxed text-ink-700">{document.provenance}</p>
        </section>
      )}

      {document.topics.some((topic) => topic.name) && (
        <section>
          <h2 className="font-serif text-lg text-ink-900">Topics</h2>
          <p className="mt-1 text-xs text-ink-500">
            Assigned by extraction from this record&apos;s own text, so they describe the document
            and are not an editorial judgement about it.
          </p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {document.topics
              .filter((topic) => topic.name)
              .map((topic) => (
                <li
                  key={topic.topic_id}
                  className="rounded-full border border-ink-300 px-3 py-1 text-xs text-ink-700"
                >
                  {topic.name}
                </li>
              ))}
          </ul>
        </section>
      )}
    </article>
  )
}
