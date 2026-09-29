import { useEffect, useRef, useState } from 'react'
import { api, type GroundedSummary, type RagAnswer } from '../lib/api'
import { ProvenanceWarning } from './Provenance'
import { CitationList } from './ui'

/**
 * The 30-second understanding of one record.
 *
 * Every line here is a sentence quoted from the record's own text: the server
 * builds the summary extractively, so this component has nothing to hedge about
 * beyond the disclosure it is required to show. When the record is too thin to
 * summarise, that is said plainly and the reader is sent to the full text rather
 * than shown a summary that would have had to be invented.
 */

/** Questions a reader can ask about a record without writing anything. */
function suggestedQuestions(documentTitle: string, documentType: string): string[] {
  const isAudio = documentType === 'audio'
  const isVisual = documentType === 'manuscript'
  const questions = [
    `What is the main argument of "${documentTitle}"?`,
    'What evidence is given to support it?',
    'Who is addressed, and what is asked of them?',
  ]
  if (isAudio) questions.push('What does the speaker emphasise most?')
  if (isVisual) questions.push('Which parts of the text were hardest to make out?')
  return questions.slice(0, 3)
}

function SpeakButton({ text }: { text: string }) {
  const [speaking, setSpeaking] = useState(false)
  const supported = typeof window !== 'undefined' && 'speechSynthesis' in window

  if (!supported) return null

  function listen() {
    const synth = window.speechSynthesis
    if (speaking) {
      synth.cancel()
      setSpeaking(false)
      return
    }
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.onend = () => setSpeaking(false)
    utterance.onerror = () => setSpeaking(false)
    synth.speak(utterance)
    setSpeaking(true)
  }

  return (
    <button
      type="button"
      onClick={listen}
      className="rounded border border-ink-300 bg-white px-3 py-1.5 text-sm text-ink-800 transition hover:border-indigo-400 hover:text-indigo-800"
    >
      {speaking ? '■ Stop' : '🔊 Listen'}
    </button>
  )
}

export function UnderstandInThirtySeconds({
  documentId,
  documentTitle,
  documentType,
  summary,
  hasFullText,
}: {
  documentId: string
  documentTitle: string
  documentType: string
  summary: GroundedSummary | null
  hasFullText: boolean
}) {
  const [askOpen, setAskOpen] = useState(false)

  if (!summary) return null

  return (
    <section
      aria-labelledby="understand-heading"
      className="space-y-4 rounded-lg border border-saffron-300 bg-saffron-50/60 p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="understand-heading" className="font-serif text-xl text-ink-900">
          🤖 Understand in 30 Seconds
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setAskOpen((open) => !open)}
            className="rounded border border-indigo-300 bg-white px-3 py-1.5 text-sm text-indigo-800 transition hover:border-indigo-500"
          >
            🤖 Ask AI
          </button>
          {summary.available && summary.main_idea && (
            <SpeakButton text={[summary.what_is_this, summary.main_idea].join(' ')} />
          )}
          {hasFullText && (
            <a
              href="#stored-text"
              className="rounded border border-ink-300 bg-white px-3 py-1.5 text-sm text-ink-800 transition hover:border-ink-500"
            >
              Read Original
            </a>
          )}
        </div>
      </div>

      {!summary.available ? (
        <p className="text-sm leading-relaxed text-ink-700">
          {summary.unavailable_reason ?? 'This record could not be summarised honestly.'}
        </p>
      ) : (
        <div className="space-y-3">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">What is this?</h3>
            <p className="mt-1 leading-relaxed text-ink-800">{summary.what_is_this}</p>
          </div>
          {summary.main_idea && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                Main idea
              </h3>
              <p className="mt-1 font-serif text-[1.05rem] leading-relaxed text-ink-900">
                {summary.main_idea}
              </p>
            </div>
          )}
          {summary.key_points.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                Key points
              </h3>
              <ul className="mt-1 space-y-1.5">
                {summary.key_points.map((point) => (
                  <li key={`${point.chunk_index}-${point.text.slice(0, 24)}`} className="flex gap-2 leading-relaxed text-ink-800">
                    <span aria-hidden="true" className="text-saffron-700">
                      •
                    </span>
                    <span>
                      {point.text}
                      {point.page_number != null && (
                        <span className="ml-1 text-xs text-ink-500">p. {point.page_number}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              Why it matters
            </h3>
            <p className="mt-1 text-sm leading-relaxed text-ink-700">
              This summary is drawn from {summary.source_chunks} indexed passage
              {summary.source_chunks === 1 ? '' : 's'} of the record
              {summary.source_characters > 0
                ? ` (${summary.source_characters.toLocaleString()} characters)`
                : ''}
              . Every sentence above is quoted from that text rather than written from general knowledge,
              so it cannot describe anything the archive does not actually hold.
            </p>
          </div>
        </div>
      )}

      <p className="text-xs italic text-ink-500">{summary.disclosure}</p>

      {askOpen && (
        <AskAboutThisDocument
          documentId={documentId}
          documentTitle={documentTitle}
          documentType={documentType}
        />
      )}
    </section>
  )
}

/**
 * Ask a question about one record.
 *
 * The request is scoped to this document by id, so evidence can only come from
 * this record and the citation list below the answer cannot point somewhere
 * else. A refusal is rendered as a first-class answer, not an error, because
 * "the record does not say" is the correct answer far more often than not.
 */
function AskAboutThisDocument({
  documentId,
  documentTitle,
  documentType,
}: {
  documentId: string
  documentTitle: string
  documentType: string
}) {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<RagAnswer | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const suggestions = suggestedQuestions(documentTitle, documentType)

  async function submit(value: string) {
    const trimmed = value.trim()
    if (trimmed.length < 3) return
    setLoading(true)
    setError(null)
    setAnswer(null)
    try {
      setAnswer(await api.askAboutDocument(trimmed, documentId))
    } catch (caught) {
      setError(caught)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-3 rounded border border-indigo-200 bg-white p-4">
      <h3 className="font-serif text-base text-ink-900">Ask AI about this document</h3>
      <p className="text-xs text-ink-500">
        Answers are drawn only from this record. If it does not address the question, the archive says
        so rather than answering from elsewhere.
      </p>

      <form
        onSubmit={(event) => {
          event.preventDefault()
          void submit(question)
        }}
        className="space-y-2"
      >
        <label htmlFor="ask-document" className="sr-only">
          Ask a question about this document
        </label>
        <textarea
          id="ask-document"
          ref={inputRef}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={2}
          maxLength={1500}
          placeholder="e.g. What does this record say about education?"
          className="w-full rounded border border-ink-300 p-2 text-sm text-ink-900 focus:border-indigo-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={loading || question.trim().length < 3}
          className="rounded bg-indigo-800 px-4 py-1.5 text-sm text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? 'Asking…' : 'Ask'}
        </button>
      </form>

      <ul className="flex flex-wrap gap-2">
        {suggestions.map((suggestion) => (
          <li key={suggestion}>
            <button
              type="button"
              onClick={() => {
                setQuestion(suggestion)
                void submit(suggestion)
              }}
              className="rounded-full border border-ink-300 px-3 py-1 text-xs text-ink-700 transition hover:border-indigo-400 hover:text-indigo-800"
            >
              {suggestion}
            </button>
          </li>
        ))}
      </ul>

      {error != null && (
        <ProvenanceWarning>
          The question could not be sent to the archive. Nothing was answered.
        </ProvenanceWarning>
      )}

      {answer && (
        <div className="space-y-3 border-t border-ink-200 pt-3">
          <p className="whitespace-pre-wrap font-serif text-[1.02rem] leading-relaxed text-ink-900">
            {answer.answer}
          </p>
          {answer.refused && <ProvenanceWarning>{answer.refusal_reason}</ProvenanceWarning>}
          {answer.citations.length > 0 && <CitationList citations={answer.citations} />}
          <p className="text-xs italic text-ink-500">{answer.disclaimer}</p>
        </div>
      )}
    </div>
  )
}
