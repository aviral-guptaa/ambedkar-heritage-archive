import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError, type RagAnswer } from '../lib/api'
import { ProvenanceWarning, VerificationBadge } from '../components/Provenance'
import { CitationList, ErrorNotice, Spinner } from '../components/ui'

const EXAMPLES = [
  'What did Ambedkar say about endogamy and exogamy?',
  'Which article did he call the heart and soul of the Constitution?',
  'What was his view on the abolition of untouchability?',
  'What did he say about women and education?',
]

/** How each answer kind is described to the reader. */
const KIND_EXPLANATION: Record<RagAnswer['answer_kind'], { label: string; tone: string; note: string }> = {
  grounded: {
    label: 'Grounded answer',
    tone: 'border-emerald-300 bg-emerald-50 text-emerald-950',
    note: 'Each point below was checked against a source whose text matches the original.',
  },
  unverified_summary: {
    label: 'Unverified summary — not a quotation',
    tone: 'border-amber-400 bg-amber-50 text-amber-950',
    note:
      'The passages this summarises were retrieved from records that have not been checked against the archival original, so they are given as a summary and not in quotation marks.',
  },
  conflicting: {
    label: 'Sources disagree',
    tone: 'border-red-300 bg-red-50 text-red-950',
    note: 'The indexed records give different accounts, so no single answer is offered.',
  },
  insufficient_evidence: {
    label: 'Not answered',
    tone: 'border-ink-300 bg-ink-100 text-ink-800',
    note: 'The archive has nothing on this question, or nothing close enough to answer it.',
  },
  refused: {
    label: 'Not answered',
    tone: 'border-ink-300 bg-ink-100 text-ink-800',
    note: 'The archive declined to answer.',
  },
}

export function AiResearch() {
  // The question lives in the URL so an answer can be linked to, cited and
  // re-opened, which is the same standard the archive applies to a record.
  const [params, setParams] = useSearchParams()
  const urlQuestion = params.get('q') ?? ''
  const [question, setQuestion] = useState(urlQuestion)
  const [answer, setAnswer] = useState<RagAnswer | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)

  const ask = useCallback(async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed) return
    setLoading(true)
    setError(null)
    setAnswer(null)
    try {
      setAnswer(await api.ask(trimmed))
    } catch (caught) {
      setError(caught)
    } finally {
      setLoading(false)
    }
  }, [])

  // A shared link answers as soon as the page opens.
  useEffect(() => {
    if (urlQuestion) void ask(urlQuestion)
  }, [ask, urlQuestion])

  function submit(text: string) {
    setQuestion(text)
    setParams(text.trim() ? { q: text.trim() } : {}, { replace: false })
    void ask(text)
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    submit(question)
  }

  const kind = answer ? KIND_EXPLANATION[answer.answer_kind] : null

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-3xl text-ink-900">Ask the archive</h1>
        <p className="mt-2 max-w-3xl text-ink-700">
          Questions are answered only from passages in the archive. Every point is followed by the
          record it came from, and the answer states plainly whether that record has been verified
          against the original. If the archive does not hold anything on the question, it says so
          rather than answering from general knowledge.
        </p>
      </header>

      <form onSubmit={onSubmit} className="space-y-3 rounded-lg border border-ink-200 bg-white p-4">
        <label className="block">
          <span className="sr-only">Your question</span>
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            rows={3}
            placeholder="Ask about caste, untouchability, the Constitution, education…"
            className="w-full resize-y rounded-md border border-ink-300 px-3 py-2 font-serif text-base focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <button
            type="submit"
            disabled={loading || !question.trim()}
            className="rounded-md bg-indigo-900 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
          >
            {loading ? 'Searching the archive…' : 'Ask'}
          </button>
          <p className="text-xs text-ink-500">Answers use only indexed passages, never a general model.</p>
        </div>
      </form>

      {!answer && !loading && error == null && (
        <div className="space-y-2">
          <p className="text-sm text-ink-600">For example:</p>
          <ul className="space-y-1.5">
            {EXAMPLES.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  onClick={() => submit(example)}
                  className="text-left text-sm text-indigo-700 underline-offset-2 hover:underline"
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {loading && <Spinner label="Retrieving passages" />}
      {error != null && <ErrorNotice error={error} />}

      {answer && kind && (
        <article className="space-y-4" aria-live="polite">
          <div className={`rounded-lg border p-4 ${kind.tone}`}>
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-semibold">{kind.label}</p>
              {answer.groundedness !== null && (
                <span className="text-xs opacity-80">
                  groundedness {answer.groundedness.toFixed(2)}
                </span>
              )}
            </div>
            <p className="mt-1.5 text-sm leading-relaxed">{kind.note}</p>
          </div>

          <div
            className={`rounded-lg border bg-white p-5 ${
              answer.answer_kind === 'unverified_summary' ? 'border-amber-300' : 'border-ink-200'
            }`}
          >
            {/*
              The answer text is rendered exactly as the API produced it. When the
              passages are unverified the API returns a summary with no quotation
              marks, so there is nothing here to strip and no wording to alter.
            */}
            <div className="whitespace-pre-wrap font-serif text-[1.05rem] leading-[1.8] text-ink-900">
              {answer.answer}
            </div>
          </div>

          {answer.disclaimer && <ProvenanceWarning>{answer.disclaimer}</ProvenanceWarning>}

          {answer.refusal_reason && (
            <p className="rounded-lg border border-ink-300 bg-ink-100 p-3 text-sm leading-relaxed text-ink-700">
              {answer.refusal_reason}
            </p>
          )}

          {answer.conflict_note && (
            <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-950">
              {answer.conflict_note}
            </p>
          )}

          {answer.answer_kind === 'insufficient_evidence' || answer.answer_kind === 'refused' ? (
            answer.citations.length > 0 && (
              <section className="rounded-lg border border-ink-200 bg-white p-4">
                <h2 className="font-serif text-lg text-ink-900">Passages that were checked</h2>
                <p className="mt-1 text-sm text-ink-600">
                  These are the passages the archive retrieved while looking for an answer. They do
                  not support an answer to this question, which is why none is given. They are
                  listed so you can judge the refusal rather than take it on trust.
                </p>
                <div className="mt-3">
                  <CitationList citations={answer.citations} />
                </div>
              </section>
            )
          ) : (
            <>
              <h2 className="font-serif text-lg text-ink-900">Sources for this answer</h2>
              <CitationList citations={answer.citations} />
            </>
          )}

          {answer.answer_kind === 'unverified_summary' && (
            <details className="rounded-lg border border-ink-200 bg-ink-50/60 p-4 text-sm">
              <summary className="cursor-pointer font-medium text-ink-800">
                How this answer was produced
              </summary>
              <div className="mt-3 space-y-2 text-ink-700">
                <p>
                  The archive retrieved passages with similar wording to the question, then
                  returned the most relevant sentences from those passages.{' '}
                  {answer.providers.llm === 'extractive' ? (
                    <>
                      No language model was involved: the wording above is wording this archive
                      already stores, selected by relevance.
                    </>
                  ) : (
                    <>
                      A language model ({answer.providers.model}) selected and ordered sentences
                      from those passages, so the wording may differ from the stored text even
                      though the citations are unchanged.
                    </>
                  )}
                </p>
                <p>
                  Because the records are unverified, the points are presented as a summary rather
                  than in quotation marks. To check any point against the original, follow the source
                  link on its citation.
                </p>
                <p className="text-xs text-ink-500">
                  Answered by {answer.providers.llm} ({answer.providers.model}) using{' '}
                  {answer.providers.retrieval} retrieval over {answer.evidence.length} passage
                  {answer.evidence.length === 1 ? '' : 's'}, in {answer.duration_ms} ms. Reference{' '}
                  <span className="font-mono">{answer.query_id}</span>.
                </p>
              </div>
            </details>
          )}

          {answer.citations.length > 0 && (
            <p className="text-xs text-ink-500">
              Every citation points to a record in the{' '}
              <Link to="/manuscripts" className="underline underline-offset-2">
                manuscripts
              </Link>{' '}
              section, with its own provenance note.
            </p>
          )}
        </article>
      )}

      {answer && answer.answer_kind === 'grounded' && (
        <p className="text-xs text-ink-500">
          Verified sources only:{' '}
          {answer.citations.map((citation, index) => (
            <span key={citation.marker}>
              {index > 0 && ', '}
              <VerificationBadge status={citation.verification_status} compact />
            </span>
          ))}
        </p>
      )}

      {error instanceof ApiError && error.status === 0 && (
        <p className="text-xs text-ink-500">
          The archive could not be reached, so no answer is shown. This is a connection problem, not
          a statement about what the archive contains.
        </p>
      )}

      <aside className="rounded-lg border border-ink-200 bg-white p-4 text-sm">
        <p className="font-medium text-ink-800">Where these answers come from</p>
        <p className="mt-1.5 text-ink-700">
          Answers are assembled from the passages listed under each response. If a passage is not in
          the archive, it is not said — including when that means declining to answer.
        </p>
      </aside>
    </div>
  )
}
