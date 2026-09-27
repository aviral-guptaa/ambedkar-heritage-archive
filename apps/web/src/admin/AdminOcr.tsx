import { useState } from 'react'
import { ApiError, adminApi, type OcrQueueItem } from '../lib/adminApi'
import { formatNumber } from '../lib/format'
import { useAdminSession } from './AdminSession'
import { useLoader } from './useLoader'
import { Action, Empty, LoadFailure, Loading, Outcome, Panel } from './AdminShell'

/**
 * Reading and correcting OCR output.
 *
 * The correction a curator types here replaces the machine's reading, and the
 * document is reindexed afterwards — so the text a reader sees and the text the
 * search answers from stay in step. The original machine output is left in the
 * version history rather than overwritten, which is what lets a reader see what
 * the machine said and what a person decided.
 */
export function AdminOcr() {
  const { can } = useAdminSession()
  const queue = useLoader(() => adminApi.ocrQueue(50))
  const [editing, setEditing] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  async function approve(item: OcrQueueItem) {
    const key = pageKey(item)
    const edited = editing[key]
    setBusy(key)
    setNotice(null)
    setFailure(null)
    try {
      const result = await adminApi.approvePage(item.document_id, item.page_number, edited ?? null)
      const reindexed = (result.reindexed ?? {}) as { chunks?: number; embedded?: number }
      setNotice(
        `Page ${item.page_number} of "${item.document_title}" approved` +
          (typeof reindexed.chunks === 'number'
            ? ` and reindexed: ${reindexed.chunks} chunks, ${reindexed.embedded ?? 0} embedded.`
            : '.'),
      )
      queue.reload()
    } catch (cause) {
      setFailure(cause instanceof ApiError ? cause.message : 'Could not approve that page.')
    } finally {
      setBusy(null)
    }
  }

  async function reprocess(item: OcrQueueItem) {
    const key = pageKey(item)
    setBusy(key)
    setNotice(null)
    setFailure(null)
    try {
      await adminApi.reprocessOcr(item.document_id, item.page_number)
      setNotice(`OCR re-run for page ${item.page_number} of "${item.document_title}".`)
      queue.reload()
    } catch (cause) {
      setFailure(
        cause instanceof ApiError
          ? cause.message
          : 'OCR could not be re-run; the page is unchanged.',
      )
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">OCR review</h1>
        <p className="mt-1 text-sm text-stone-600">
          {queue.data
            ? `${formatNumber(queue.data.total)} page(s) are waiting for a person to read them.`
            : 'Pages waiting for review.'}
          Approving a correction rebuilds the document's text and vectors.
        </p>
      </div>

      <Outcome notice={notice} failure={failure} />

      {queue.loading ? (
        <Loading />
      ) : queue.error ? (
        <LoadFailure error={queue.error} requestId={queue.requestId} />
      ) : queue.data?.items.length === 0 ? (
        <Panel title="Review queue">
          <Empty>No pages are waiting for review. Anything OCR could not read with confidence will appear here.</Empty>
        </Panel>
      ) : (
        <div className="space-y-4">
          {queue.data?.items.map((item) => {
            const key = pageKey(item)
            const confidence = item.ocr_confidence
            return (
              <Panel
                key={key}
                title={`${item.document_title} — page ${item.page_number}`}
                action={
                  <div className="flex items-center gap-2">
                    <ConfidenceBadge value={confidence} />
                    {can('ocr:run') && (
                      <Action
                        disabled={busy === key}
                        onClick={() => void reprocess(item)}
                      >
                        Re-run OCR
                      </Action>
                    )}
                    {can('ocr:approve') && (
                      <Action
                        disabled={busy === key}
                        tone="danger"
                        onClick={() => void approve(item)}
                      >
                        Approve page
                      </Action>
                    )}
                  </div>
                }
              >
                <p className="mb-2 text-xs text-stone-600">
                  Engine {item.ocr_engine ?? 'unrecorded'}
                  {item.ocr_language ? ` · language ${item.ocr_language}` : ''} · document status{' '}
                  {item.document_ocr_status}
                </p>
                <label className="block text-sm">
                  <span className="font-medium text-stone-800">
                    Read the page and correct anything the machine got wrong
                  </span>
                  <textarea
                    value={editing[key] ?? item.text}
                    onChange={(event) =>
                      setEditing((current) => ({ ...current, [key]: event.target.value }))
                    }
                    rows={8}
                    className="mt-1 w-full rounded-md border border-stone-300 p-3 font-mono text-sm"
                  />
                </label>
                {editing[key] !== undefined && (
                  <p className="mt-1 text-xs text-stone-600">
                    Saving sends this text instead of the machine's reading. The original is kept in the
                    version history.
                  </p>
                )}
              </Panel>
            )
          })}
        </div>
      )}
    </div>
  )
}

function pageKey(item: OcrQueueItem): string {
  return `${item.document_id}:${item.page_number}`
}

/**
 * A confidence figure is shown next to the text so a curator knows how much
 * attention it needs. A missing figure is reported as unknown rather than
 * rounded to a reassuring number.
 */
function ConfidenceBadge({ value }: { value: number | null }) {
  if (value === null || value === undefined) {
    return (
      <span
        title="No confidence figure was recorded for this page"
        className="rounded border border-stone-300 px-2 py-0.5 text-xs text-stone-600"
      >
        confidence unknown
      </span>
    )
  }
  // The stored figure may be a fraction or a percentage, depending on the
  // engine that produced it.
  const percent = Math.round(value <= 1 ? value * 100 : value)
  // Written out rather than interpolated: a class name built at runtime is
  // removed by the CSS build and silently renders unstyled.
  const tone =
    percent >= 90
      ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
      : percent >= 70
        ? 'border-amber-300 bg-amber-50 text-amber-900'
        : 'border-red-300 bg-red-50 text-red-900'
  return (
    <span className={`rounded border px-2 py-0.5 text-xs ${tone}`}>{percent}% confidence</span>
  )
}
