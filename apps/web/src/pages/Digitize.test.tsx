import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Digitize } from './Digitize'
import { UnderstandInThirtySeconds } from '../components/UnderstandIn30Seconds'
import { renderWithProviders } from '../test/providers'
import type { DigitizeCapabilities, DigitizeResult, GroundedSummary } from '../lib/api'
import * as api from '../lib/api'

/**
 * These tests are about what the interface refuses to claim.
 *
 * The archive's whole premise is that a reader can trust a statement about a
 * record only when the record supports it, so the interesting cases are the
 * ones where the honest answer is "no", "not configured", or "not enough text".
 * Tests that only check the happy path would pass against a page that
 * fabricated a translation.
 */

const capabilities: DigitizeCapabilities = {
  ocr_available: true,
  ocr_detail: 'tesseract active',
  translation_available: false,
  translation_detail:
    'Translation is not configured on this deployment, so the OCR text is returned in its original language.',
  accepted_extensions: ['.jpg', '.jpeg', '.png', '.pdf'],
  max_upload_bytes: 32 * 1024 * 1024,
  max_pages: 20,
  languages: ['eng', 'hin', 'mar'],
}

const groundedSummary: GroundedSummary = {
  document_id: 'doc-1',
  document_slug: 'caste-in-india',
  available: true,
  disclosure:
    'AI-generated summary based on this archival record. Every line is quoted from the record’s own text.',
  method: 'extractive',
  model_id: null,
  what_is_this: 'A speech held by the archive (from 1916).',
  main_idea: 'It is a truism that in India the caste system has operated as a mechanism.',
  key_points: [
    { text: 'The Brahmans have always claimed their superiority.', page_number: 1, chunk_index: 0 },
  ],
  source_characters: 1200,
  source_chunks: 4,
  unavailable_reason: null,
  citations: [],
}

const ocrOnlyResult: DigitizeResult = {
  document_id: 'doc-2',
  document_slug: 'digitized-page-01-abcd1234',
  title: 'Untitled manuscript (page-01.png)',
  filename: 'page-01.png',
  sha256: 'a'.repeat(64),
  page_count: 1,
  ocr_status: 'review',
  ocr_engine: 'tesseract',
  confidence: 0.82,
  detected_language: 'hin',
  original_text: 'यह मूल पाठ है',
  english_text: null,
  translation_status: 'unavailable',
  translation_detail: 'Translation is not configured on this deployment.',
  warnings: [],
  is_draft: true,
  disclosure: 'Text was read from your upload by OCR and may contain errors.',
}

function stubApi(overrides: Partial<typeof api> = {}) {
  vi.spyOn(api.api, 'digitizeCapabilities').mockResolvedValue(capabilities)
  vi.spyOn(api.api, 'documentSummary').mockResolvedValue(groundedSummary)
  if (Object.keys(overrides).length > 0) Object.assign(api.api, overrides)
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Understand in 30 Seconds', () => {
  it('shows only the sentences the server quoted, with its disclosure', async () => {
    renderWithProviders(
      <UnderstandInThirtySeconds
        documentId="doc-1"
        documentTitle="Castes in India"
        documentType="speech"
        summary={groundedSummary}
        hasFullText
      />,
    )

    expect(screen.getByText('🤖 Understand in 30 Seconds')).toBeInTheDocument()
    expect(screen.getByText(/main idea/i)).toBeInTheDocument()
    expect(
      screen.getByText('It is a truism that in India the caste system has operated as a mechanism.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('The Brahmans have always claimed their superiority.', { exact: false }),
    ).toBeInTheDocument()
    // The disclosure is not optional chrome: without it a reader would take
    // quoted text for a paraphrase.
    expect(screen.getByText(/AI-generated summary based on this archival record/)).toBeInTheDocument()
  })

  it('offers Listen, Read Original, and Ask AI', () => {
    renderWithProviders(
      <UnderstandInThirtySeconds
        documentId="doc-1"
        documentTitle="Castes in India"
        documentType="speech"
        summary={groundedSummary}
        hasFullText
      />,
    )
    expect(screen.getByRole('button', { name: /ask ai/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /read original/i })).toHaveAttribute(
      'href',
      '#stored-text',
    )
  })

  it('says why a record could not be summarised instead of summarising it anyway', () => {
    const tooShort: GroundedSummary = {
      ...groundedSummary,
      available: false,
      main_idea: null,
      key_points: [],
      unavailable_reason:
        'This record has only 180 characters of indexed text, which is not enough to summarise honestly.',
    }
    renderWithProviders(
      <UnderstandInThirtySeconds
        documentId="doc-3"
        documentTitle="A fragment"
        documentType="manuscript"
        summary={tooShort}
        hasFullText={false}
      />,
    )
    expect(screen.getByText(/not enough to summarise honestly/)).toBeInTheDocument()
    // No main idea, and no "Listen" button reading a summary that does not exist.
    expect(screen.queryByText(/main idea/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /listen/i })).toBeNull()
    expect(screen.queryByRole('link', { name: /read original/i })).toBeNull()
  })

  it('scopes a question to this document by id', async () => {
    const askSpy = vi.spyOn(api.api, 'askAboutDocument').mockResolvedValue({
      query_id: 'q1',
      answer: 'The record argues that the caste system is a mechanism for subordination.',
      answer_kind: 'grounded',
      answer_language: 'en',
      refused: false,
      refusal_reason: null,
      refusal_code: null,
      groundedness: 0.9,
      evidence: [],
      citations: [],
      claims: [],
      conflicting_evidence: false,
      conflict_note: null,
      providers: { stt: 'none', tts: 'none', translation: 'none' },
      duration_ms: 12,
      disclaimer: 'This answer is assembled only from the archive’s indexed sources.',
    } as unknown as Awaited<ReturnType<typeof api.api.askAboutDocument>>)

    renderWithProviders(
      <UnderstandInThirtySeconds
        documentId="doc-1"
        documentTitle="Castes in India"
        documentType="speech"
        summary={groundedSummary}
        hasFullText
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /ask ai/i }))
    fireEvent.change(
      screen.getByLabelText(/ask a question about this document/i),
      { target: { value: 'What is the argument?' } },
    )
    fireEvent.click(screen.getByRole('button', { name: /^ask$/i }))

    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    // The id is passed as its own argument, so the request cannot be built
    // with the scope omitted by accident.
    expect(askSpy).toHaveBeenCalledWith('What is the argument?', 'doc-1')
    expect(
      await screen.findByText(/argues that the caste system is a mechanism/),
    ).toBeInTheDocument()
  })
})

describe('Digitise', () => {
  it('states up front that translation is not configured', async () => {
    stubApi()
    renderWithProviders(<Digitize />)

    expect(await screen.findByText('Digitise')).toBeInTheDocument()
    expect(
      screen.getByText(/Translation is not configured on this deployment/),
    ).toBeInTheDocument()
  })

  it('warns that OCR is unavailable and will not accept an upload', async () => {
    vi.spyOn(api.api, 'digitizeCapabilities').mockResolvedValue({
      ...capabilities,
      ocr_available: false,
      ocr_detail: 'No OCR engine installed.',
    })
    renderWithProviders(<Digitize />)

    expect(await screen.findByText(/OCR is not available on this deployment/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload and read/i })).toBeDisabled()
  })

  it('never shows English text when no translation provider is configured', async () => {
    vi.spyOn(api.api, 'digitizeCapabilities').mockResolvedValue(capabilities)
    vi.spyOn(api.api, 'digitize').mockResolvedValue(ocrOnlyResult)

    renderWithProviders(<Digitize />)
    const file = new File([new Uint8Array([1, 2, 3])], 'page-01.png', { type: 'image/png' })
    fireEvent.change(await screen.findByLabelText(/manuscript image or pdf/i), {
      target: { files: [file] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload and read/i }))

    // The OCR text appears, labelled as the original language. It is shown in
    // the main panel and again in the collapsed disclosure, so the assertion
    // is that it appears at all, not that it appears exactly once.
    expect((await screen.findAllByText('यह मूल पाठ है')).length).toBeGreaterThan(0)
    expect(screen.getByText(/ocr text · original language/i)).toBeInTheDocument()
    // And there is no English panel pretending a translation exists.
    expect(screen.queryByText(/english translation/i)).toBeNull()
  })

  it('reports an unreadable file rather than showing empty text as a result', async () => {
    vi.spyOn(api.api, 'digitizeCapabilities').mockResolvedValue(capabilities)
    vi.spyOn(api.api, 'digitize').mockResolvedValue({
      ...ocrOnlyResult,
      original_text: '   ',
      ocr_status: 'failed',
    })

    renderWithProviders(<Digitize />)
    const file = new File([new Uint8Array([1])], 'faded.png', { type: 'image/png' })
    fireEvent.change(await screen.findByLabelText(/manuscript image or pdf/i), {
      target: { files: [file] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload and read/i }))

    expect(await screen.findByText(/OCR read no text from this file/)).toBeInTheDocument()
  })

  it('refuses a file above the limit before uploading it', async () => {
    const digitizeSpy = vi.spyOn(api.api, 'digitize')
    stubApi()

    renderWithProviders(<Digitize />)
    const tooBig = new File([new Uint8Array(1024)], 'huge.png', { type: 'image/png' })
    Object.defineProperty(tooBig, 'size', { value: capabilities.max_upload_bytes + 1 })

    fireEvent.change(await screen.findByLabelText(/manuscript image or pdf/i), {
      target: { files: [tooBig] },
    })

    expect(await screen.findByText(/above the 32 MB limit/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload and read/i })).toBeDisabled()
    expect(digitizeSpy).not.toHaveBeenCalled()
  })

  it('shows the preserved original beside the text and says the upload is a draft', async () => {
    vi.spyOn(api.api, 'digitizeCapabilities').mockResolvedValue(capabilities)
    vi.spyOn(api.api, 'digitize').mockResolvedValue(ocrOnlyResult)

    renderWithProviders(<Digitize />)
    const file = new File([new Uint8Array([1, 2, 3])], 'page-01.png', { type: 'image/png' })
    fireEvent.change(await screen.findByLabelText(/manuscript image or pdf/i), {
      target: { files: [file] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload and read/i }))

    expect(await screen.findByText(/preserved as uploaded/)).toBeInTheDocument()
    expect(screen.getByText(/It is not published and does not appear in search/)).toBeInTheDocument()
    // The four stages are all present, so the pipeline is legible before and after.
    for (const label of ['1. Upload', '2. OCR', '3. Translate', '4. Review']) {
      expect(screen.getByText(new RegExp(label.replace('.', '\\.')))).toBeInTheDocument()
    }
  })
})
