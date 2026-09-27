import type { ReactNode } from 'react'
import { verificationLabel, type VerificationStatus } from '../lib/api'
import { PAGE_UNAVAILABLE, hostOf } from '../lib/format'
import { useLanguage } from '../lib/LanguageContext'
import { interpolate } from '../lib/i18n'

/**
 * The status of a piece of text, stated wherever that text appears.
 *
 * This is the single most important component in the application: it is what
 * stops a modern retelling being read as Dr. Ambedkar's own words.
 */
export function VerificationBadge({
  status,
  quoteVerified,
  compact = false,
}: {
  status: VerificationStatus | string | null | undefined
  quoteVerified?: boolean | null
  compact?: boolean
}) {
  const { t } = useLanguage()
  const verified = status === 'verified_primary'
  const classes = verified
    ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
    : 'border-amber-400 bg-amber-50 text-amber-900'
  const label = verified
    ? t('verification.verified')
    : quoteVerified === false
      ? t('verification.notQuotable')
      : t('verification.unverifiedText')
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${classes}`}
      title={verificationLabel(status, t)}
    >
      <span aria-hidden="true">{verified ? '✓' : '!'}</span>
      {compact ? (verified ? t('verification.compactVerified') : t('verification.compactUnverified')) : label}
    </span>
  )
}

/** A standing reminder of what the archive can and cannot vouch for. */
export function CorpusNotice({ compact = false }: { compact?: boolean }) {
  const { t } = useLanguage()
  return (
    <aside className="rounded-lg border border-amber-300 bg-amber-50/80 p-4 text-sm text-amber-950">
      <p className="font-semibold">{t('corpus.heading')}</p>
      <p className="mt-1.5 leading-relaxed">{t('corpus.body')}</p>
      <p className="mt-1.5 leading-relaxed">{t('corpus.inspection')}</p>
      {!compact && (
        <p className="mt-2 text-amber-900/80">
          {interpolate(t('corpus.archives'), { host: hostOf('https://www.columbia.edu') })}
        </p>
      )}
    </aside>
  )
}

/** The per-citation warning, shown wherever a passage is presented. */
export function ProvenanceWarning({ children }: { children?: ReactNode }) {
  return (
    <p className="flex gap-2 rounded-md border-l-4 border-amber-400 bg-amber-50/70 px-3 py-2 text-xs leading-relaxed text-amber-950">
      <span aria-hidden="true" className="font-bold">
        !
      </span>
      <span>{children}</span>
    </p>
  )
}

/**
 * Text that has not been verified.
 *
 * `children` is rendered as written, with no surrounding quotation marks: adding
 * them would assert an exact wording that nobody has checked. The warning above
 * the text says why.
 */
export function UnverifiedText({ children }: { children: ReactNode }) {
  const { t } = useLanguage()
  return (
    <div className="space-y-2">
      <ProvenanceWarning>{t('provenance.summaryNotQuotation')}</ProvenanceWarning>
      <div className="whitespace-pre-wrap font-serif text-[1.02rem] leading-[1.75] text-ink-900">
        {children}
      </div>
    </div>
  )
}

/** Where a passage sits, or an honest statement that it does not know. */
export function SourcePointer({
  pageNumber,
  unavailable,
  sourceUrl,
  sourceReference,
  sourceName,
}: {
  pageNumber: number | null | undefined
  unavailable: boolean
  sourceUrl?: string | null
  sourceReference?: string | null
  sourceName?: string | null
}) {
  const { t } = useLanguage()
  // `unavailable` is accepted because callers pass the API's field straight
  // through, but it is never consulted: a missing page is reported as missing
  // either way, so there is no flag a caller could set to suppress the
  // explanation.
  void unavailable
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-600">
      <span className={pageNumber == null ? 'italic text-amber-800' : undefined}>
        {pageNumber == null
          ? t('page.unavailable')
          : interpolate(t('page.cited'), { number: pageNumber })}
      </span>
      {sourceReference && <span className="font-mono text-[0.7rem]">{sourceReference}</span>}
      {sourceUrl ? (
        <a
          className="underline decoration-dotted underline-offset-2 hover:text-indigo-700"
          href={sourceUrl}
          target="_blank"
          rel="noreferrer noopener"
        >
          {sourceName ?? hostOf(sourceUrl)}
        </a>
      ) : (
        <span className="italic">{t('source.noneRecorded')}</span>
      )}
    </span>
  )
}

export { PAGE_UNAVAILABLE }
