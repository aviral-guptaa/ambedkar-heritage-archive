import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type DigitizeCapabilities,
  type DigitizeResult,
} from '../lib/api'
import { ErrorNotice, Spinner } from '../components/ui'
import { ProvenanceWarning } from '../components/Provenance'

/**
 * Digitise a manuscript: upload, OCR, translate, review.
 *
 * The four stages are always shown as four stages, and each one reports what
 * actually happened. Where a provider is not configured on this deployment the
 * stage says so and the page does not pretend — an honest "not configured" is
 * the correct outcome, and a fabricated English translation would be worse than
 * no translation at all.
 *
 * On a wide screen the original sits beside the text, because reading a
 * transcription while checking it against the scan is the whole point. On a
 * phone the two stack and can be toggled, since side by side is unusable there.
 */

type Stage = 'upload' | 'ocr' | 'translate' | 'review'

const STAGES: Array<{ id: Stage; label: string }> = [
  { id: 'upload', label: 'Upload' },
  { id: 'ocr', label: 'OCR' },
  { id: 'translate', label: 'Translate' },
  { id: 'review', label: 'Review' },
]

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function Digitize() {
  const [capabilities, setCapabilities] = useState<DigitizeCapabilities | null>(null)
  const [capabilitiesError, setCapabilitiesError] = useState<unknown>(null)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [language, setLanguage] = useState('')
  const [result, setResult] = useState<DigitizeResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [side, setSide] = useState<'original' | 'english'>('english')
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .digitizeCapabilities()
      .then((value) => {
        if (!cancelled) setCapabilities(value)
      })
      .catch((caught) => {
        if (!cancelled) setCapabilitiesError(caught)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (capabilitiesError != null) {
    return (
      <div className="space-y-4">
        <ErrorNotice error={capabilitiesError} />
        <p className="text-sm text-ink-600">
          Digitising needs to know what this deployment can do before it will accept an upload.
        </p>
      </div>
    )
  }
  if (!capabilities) return <Spinner label="Checking what digitising supports" />

  const accepted = capabilities.accepted_extensions.join(', ')
  const maxMb = Math.round(capabilities.max_upload_bytes / (1024 * 1024))
  const oversize = file != null && file.size > capabilities.max_upload_bytes

  async function submit() {
    if (!file || oversize) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const value = await api.digitize(file, {
        title: title.trim() || undefined,
        sourceLanguage: language || undefined,
      })
      setResult(value)
      setSide(value.english_text ? 'english' : 'original')
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  const stage: Stage = result
    ? result.translation_status === 'complete'
      ? 'review'
      : 'ocr'
    : file
      ? 'upload'
      : 'upload'

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="font-serif text-3xl leading-tight text-ink-900 md:text-4xl">Digitise</h1>
        <p className="max-w-2xl leading-relaxed text-ink-700">
          Upload a scanned manuscript and the archive will read the text from it. The original is
          preserved exactly as you sent it and is never modified; OCR text is a reading aid, not a
          verified transcription, and should be checked against the scan before it is relied on.
        </p>
      </header>

      {!capabilities.ocr_available && (
        <ProvenanceWarning>
          OCR is not available on this deployment, so no text can be read from an upload. The page
          is shown so the limitation is visible rather than discovered mid-upload.
        </ProvenanceWarning>
      )}

      {/*
        Both provider limits are stated before the upload button, not after the
        result. A reader who only learns that translation is unavailable once
        their file has been uploaded has already been misled about what this
        page does.
      */}
      {capabilities.ocr_available && !capabilities.translation_available && (
        <ProvenanceWarning>{capabilities.translation_detail}</ProvenanceWarning>
      )}

      <ol className="flex flex-wrap gap-2" aria-label="Digitising stages">
        {STAGES.map((item, index) => {
          const reached = item.id === stage
          return (
            <li
              key={item.id}
              aria-current={reached ? 'step' : undefined}
              className={`rounded-full border px-3 py-1 text-sm ${
                reached
                  ? 'border-indigo-500 bg-indigo-50 font-semibold text-indigo-900'
                  : 'border-ink-200 bg-white text-ink-500'
              }`}
            >
              <span className="mr-1 font-mono text-xs">{index + 1}.&nbsp;</span>
              {item.label}
            </li>
          )
        })}
      </ol>

      <section
        aria-labelledby="upload-heading"
        className="space-y-4 rounded-lg border border-ink-200 bg-white p-5"
      >
        <h2 id="upload-heading" className="font-serif text-xl text-ink-900">
          1. Upload
        </h2>
        <p className="text-sm text-ink-600">
          Accepted: {accepted}. Up to {maxMb} MB, {capabilities.max_pages} pages.
        </p>

        <div className="space-y-3">
          <div>
            <label htmlFor="digitize-file" className="text-sm font-medium text-ink-800">
              Manuscript image or PDF
            </label>
            <input
              id="digitize-file"
              ref={inputRef}
              type="file"
              accept={capabilities.accepted_extensions.join(',')}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="mt-1 block w-full text-sm text-ink-700 file:mr-3 file:rounded file:border file:border-ink-300 file:bg-ink-50 file:px-3 file:py-1.5 file:text-sm file:text-ink-800"
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="digitize-title" className="text-sm font-medium text-ink-800">
                Title (optional)
              </label>
              <input
                id="digitize-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                maxLength={200}
                className="mt-1 w-full rounded border border-ink-300 p-2 text-sm"
              />
            </div>
            <div>
              <label htmlFor="digitize-language" className="text-sm font-medium text-ink-800">
                Language of the manuscript
              </label>
              <select
                id="digitize-language"
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                className="mt-1 w-full rounded border border-ink-300 p-2 text-sm"
              >
                <option value="">Detect automatically</option>
                {capabilities.languages.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {file && (
            <p className="text-sm text-ink-700">
              Selected: <span className="font-medium">{file.name}</span> ({formatBytes(file.size)})
            </p>
          )}
          {oversize && (
            <p className="text-sm text-amber-800">
              This file is {formatBytes(file?.size ?? 0)}, above the {maxMb} MB limit. Nothing was
              uploaded.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!file || oversize || busy || !capabilities.ocr_available}
              className="rounded bg-indigo-800 px-4 py-2 text-sm text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? 'Reading the manuscript…' : 'Upload and read'}
            </button>
            {file && !busy && (
              <button
                type="button"
                onClick={() => {
                  setFile(null)
                  setResult(null)
                  setError(null)
                  if (inputRef.current) inputRef.current.value = ''
                }}
                className="text-sm text-ink-600 underline underline-offset-2"
              >
                Clear
              </button>
            )}
          </div>
        </div>
      </section>

      {error != null && (
        <ErrorNotice error={error} onRetry={() => void submit()} />
      )}

      {result && (
        <>
          <section
            aria-labelledby="ocr-heading"
            className="space-y-3 rounded-lg border border-ink-200 bg-white p-5"
          >
            <h2 id="ocr-heading" className="font-serif text-xl text-ink-900">
              2. OCR
            </h2>
            <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
              <div className="flex justify-between gap-3">
                <dt className="text-ink-600">Engine</dt>
                <dd className="text-ink-900">{result.ocr_engine ?? 'not reported'}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-ink-600">Confidence</dt>
                <dd className="text-ink-900">
                  {result.confidence != null ? `${Math.round(result.confidence * 100)}%` : 'not reported'}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-ink-600">Pages</dt>
                <dd className="text-ink-900">{result.page_count}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-ink-600">Checksum</dt>
                <dd className="truncate font-mono text-xs text-ink-700" title={result.sha256}>
                  {result.sha256.slice(0, 16)}…
                </dd>
              </div>
            </dl>
            {result.warnings.map((warning) => (
              <p key={warning} className="text-sm text-amber-800">
                {warning}
              </p>
            ))}
          </section>

          <section
            aria-labelledby="translate-heading"
            className="space-y-3 rounded-lg border border-ink-200 bg-white p-5"
          >
            <h2 id="translate-heading" className="font-serif text-xl text-ink-900">
              3. Translate
            </h2>
            {capabilities.translation_available ? (
              result.translation_status === 'complete' ? (
                <p className="text-sm text-ink-700">
                  English text was produced by the configured translation model. It is a machine
                  rendering, not a translation of record, and the original text is kept unchanged
                  beside it.
                </p>
              ) : (
                <p className="text-sm text-ink-700">{result.translation_detail}</p>
              )
            ) : (
              <ProvenanceWarning>{capabilities.translation_detail}</ProvenanceWarning>
            )}
            {result.translation_status !== 'complete' && (
              <p className="text-sm text-ink-600">
                The text below is exactly what OCR read, in the original language. Nothing has been
                translated.
              </p>
            )}
          </section>

          <section aria-labelledby="review-heading" className="space-y-3">
            <h2 id="review-heading" className="font-serif text-xl text-ink-900">
              4. Review
            </h2>
            <p className="text-sm text-ink-600">{result.disclosure}</p>

            <div className="flex gap-2 md:hidden" role="group" aria-label="Choose what to show">
              <button
                type="button"
                onClick={() => setSide('original')}
                aria-pressed={side === 'original'}
                className={`rounded px-3 py-1.5 text-sm ${
                  side === 'original'
                    ? 'bg-indigo-800 text-white'
                    : 'border border-ink-300 bg-white text-ink-700'
                }`}
              >
                Original
              </button>
              <button
                type="button"
                onClick={() => setSide('english')}
                aria-pressed={side === 'english'}
                className={`rounded px-3 py-1.5 text-sm ${
                  side === 'english'
                    ? 'bg-indigo-800 text-white'
                    : 'border border-ink-300 bg-white text-ink-700'
                }`}
              >
                {result.english_text ? 'English' : 'OCR text'}
              </button>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className={`space-y-2 ${side === 'original' ? '' : 'hidden md:block'}`}>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                  Original · {result.filename}
                </h3>
                <p className="text-xs text-ink-500">
                  The uploaded file is preserved unchanged; it is not shown here because the archive
                  does not re-render an arbitrary upload for the browser.
                </p>
                <dl className="rounded border border-ink-200 bg-white p-3 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-ink-600">sha256</dt>
                    <dd className="break-all font-mono text-xs text-ink-700">{result.sha256}</dd>
                  </div>
                  <div className="mt-1 flex justify-between gap-3">
                    <dt className="text-ink-600">Status</dt>
                    <dd className="text-ink-900">preserved as uploaded</dd>
                  </div>
                </dl>
              </div>

              <div className={`space-y-2 ${side === 'english' ? '' : 'hidden md:block'}`}>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                  {result.english_text ? 'English translation' : 'OCR text · original language'}
                </h3>
                {result.english_text ? (
                  <p className="whitespace-pre-wrap rounded border border-ink-200 bg-white p-4 font-serif leading-[1.8] text-ink-900">
                    {result.english_text}
                  </p>
                ) : result.original_text.trim() ? (
                  <p className="whitespace-pre-wrap rounded border border-ink-200 bg-white p-4 font-serif leading-[1.8] text-ink-900">
                    {result.original_text}
                  </p>
                ) : (
                  <p className="rounded border border-amber-300 bg-amber-50/70 p-4 text-sm text-amber-950">
                    OCR read no text from this file. That is reported as an empty reading rather than
                    filled in, because the archive cannot invent a transcription.
                  </p>
                )}
                {result.original_text.trim() && (
                  <details className="rounded border border-ink-200 bg-white p-3">
                    <summary className="cursor-pointer text-sm text-ink-700">
                      Show OCR text as read{result.english_text ? ' (original language)' : ''}
                    </summary>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink-800">
                      {result.original_text}
                    </p>
                  </details>
                )}
              </div>
            </div>

            <p className="text-sm text-ink-600">
              Saved as a private draft
              {result.document_slug ? (
                <>
                  {' '}
                  (<span className="font-mono text-xs">{result.document_slug}</span>)
                </>
              ) : null}
              . It is not published and does not appear in search until an archivist reviews it.{' '}
              <Link to="/manuscripts" className="text-indigo-700 underline underline-offset-2">
                Browse the published manuscripts
              </Link>
              .
            </p>
          </section>
        </>
      )}
    </div>
  )
}
