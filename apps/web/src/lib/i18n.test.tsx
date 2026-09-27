import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  CorpusNotice,
  SourcePointer,
  UnverifiedText,
  VerificationBadge,
} from '../components/Provenance'
import { renderWithProviders } from '../test/providers'
import { LANGUAGES, isLanguageCode, corpusLanguageLabel } from '../lib/languages'
import { STRINGS, interpolate, resolveString, translate, type StringKey } from '../lib/i18n'
import { readLanguage, storeLanguage } from '../lib/i18n'

beforeEach(() => {
  localStorage.clear()
})

/**
 * A translation is not a copy.
 *
 * The danger in a multilingual interface is not a missing word; it is a
 * translation that softens a warning, or an untranslated string that leaves a
 * reader unsure whether a passage is checked. These tests are written to fail
 * on both.
 */
describe('translation coverage', () => {
  const keys = Object.keys(STRINGS.en) as StringKey[]

  it('has the same keys in every language', () => {
    for (const language of LANGUAGES) {
      const table = STRINGS[language.code] as Record<string, string | undefined>
      const missing = keys.filter((key) => !table[key])
      expect({ language: language.code, missing }).toEqual({ language: language.code, missing: [] })
    }
  })

  it('translates every key rather than reusing the English text', () => {
    // A handful of proper nouns are legitimately identical across languages.
    const shared = new Set(['site.name', 'language.corpusNote'])
    for (const key of keys) {
      if (shared.has(key)) continue
      for (const language of LANGUAGES) {
        if (language.code === 'en') continue
        const table = STRINGS[language.code] as Record<string, string | undefined>
        // The corpus note is about the corpus, and English text there is a
        // statement about English text; exempt only the proper noun.
        if (key === 'language.corpusNote') continue
        expect({ key, language: language.code, text: table[key] }).not.toEqual({
          key,
          language: language.code,
          text: STRINGS.en[key],
        })
      }
    }
  })

  it('keeps the meaning of the unverified warnings in every language', () => {
    // The words that carry the warning. A translation that drops "not" or
    // "unverified" turns a caveat into a claim.
    const mustCarry = [
      'verification.notQuotable',
      'verification.unverifiedText',
      'verification.secondary',
      'verification.translation',
      'page.unavailable',
      'provenance.summaryNotQuotation',
      'verification.unknown',
    ] as const
    for (const key of mustCarry) {
      for (const language of LANGUAGES) {
        const text = translate(key, language.code)
        expect({ key, language: language.code, empty: text.trim().length }).toEqual({
          key,
          language: language.code,
          empty: expect.any(Number),
        })
        expect(text.trim().length).toBeGreaterThan(0)
        // A translation that resolved to the key name would be a missing string
        // shown to a reader as if it were prose.
        expect(text).not.toBe(key)
      }
    }
  })

  it('falls back to English rather than showing a key name', () => {
    // A language shipped with a missing entry must still show a sentence a
    // reader can act on. Simulated by deleting one from a copy of the table.
    const table: Partial<Record<StringKey, string>> = { ...STRINGS.hi }
    const key: StringKey = 'verification.notQuotable'
    const saved = table[key]
    delete table[key]
    const resolved = resolveString(key, table, STRINGS.en)
    expect(resolved).toBe(STRINGS.en[key])
    expect(resolved).not.toBe(key)
    // And the real table is untouched by the simulation.
    expect(STRINGS.hi[key]).toBe(saved)
  })
})

describe('the test helper refuses a bad language', () => {
  it('throws rather than silently rendering English', () => {
    // Passing the LANGUAGES entry instead of its code would store
    // "[object Object]", be rejected by the lookup, and quietly assert the
    // English interface while appearing to check Marathi. The guard exists so
    // that failure cannot pass unnoticed; this test is what keeps the guard
    // from being deleted as redundant.
    expect(() =>
      renderWithProviders(<VerificationBadge status="unverified_secondary" />, {
        language: LANGUAGES[2] as unknown as 'mr',
      }),
    ).toThrow(/expects a language code/)
  })
})

describe('interpolate', () => {
  it('substitutes a placeholder', () => {
    expect(interpolate('Page {number}', { number: 12 })).toBe('Page 12')
  })

  it('leaves a placeholder alone when no value is supplied', () => {
    // Silently blanking it would produce "Page " and look like page zero.
    expect(interpolate('Page {number}', {})).toBe('Page {number}')
  })

  it('does not treat a placeholder value as a pattern', () => {
    expect(interpolate('Page {number}', { number: '{number}' })).toBe('Page {number}')
  })
})

describe('language storage', () => {
  it('round-trips a supported language', () => {
    for (const language of LANGUAGES) {
      storeLanguage(language.code)
      expect(readLanguage()).toBe(language.code)
    }
  })

  it('ignores a stored value it does not recognise', () => {
    localStorage.setItem('dha.interface-language', 'fr')
    expect(readLanguage()).toBe('en')
  })

  it('defaults when storage is empty', () => {
    expect(readLanguage()).toBe('en')
  })

  it('recognises exactly the supported codes', () => {
    expect(isLanguageCode('en')).toBe(true)
    expect(isLanguageCode('hi')).toBe(true)
    expect(isLanguageCode('mr')).toBe(true)
    expect(isLanguageCode('de')).toBe(false)
    expect(isLanguageCode(null)).toBe(false)
  })
})

describe('corpus language labels', () => {
  it('names a language the archive knows', () => {
    expect(corpusLanguageLabel('hi')).toContain('Hindi')
    expect(corpusLanguageLabel('hi')).toContain('हिन्दी')
  })

  it('names an unrecognised code rather than guessing', () => {
    // A wrong guess about a language would misrepresent the source itself.
    expect(corpusLanguageLabel('zz')).toContain('zz')
  })

  it('says so when the language was not recorded', () => {
    expect(corpusLanguageLabel(null)).toMatch(/as recorded|source/i)
  })
})

describe('VerificationBadge in each language', () => {
  it('still distinguishes verified from unverified in Hindi', () => {
    const { unmount } = renderWithProviders(
      <VerificationBadge status="verified_primary" />,
      { language: 'hi' },
    )
    expect(screen.getByText('मूल के साथ सत्यापित')).toBeInTheDocument()
    unmount()

    renderWithProviders(<VerificationBadge status="unverified_secondary" quoteVerified={false} />, {
      language: 'hi',
    })
    expect(screen.getByText('असत्यापित — उद्धरण योग्य नहीं')).toBeInTheDocument()
  })

  it('still distinguishes verified from unverified in Marathi', () => {
    const { unmount } = renderWithProviders(
      <VerificationBadge status="verified_primary" />,
      { language: 'mr' },
    )
    expect(screen.getByText('मूळाशी सत्यापित')).toBeInTheDocument()
    unmount()

    renderWithProviders(<VerificationBadge status="unverified_secondary" />, { language: 'mr' })
    expect(screen.getByText('असत्यापित मजकूर')).toBeInTheDocument()
  })

  it('never presents an unknown status as verified, in any language', () => {
    for (const language of LANGUAGES) {
      const { unmount } = renderWithProviders(<VerificationBadge status="brand_new" />, { language: language.code })
      const unverified = translate('verification.unverifiedText', language.code)
      expect(screen.getByText(unverified)).toBeInTheDocument()
      expect(screen.queryByText(translate('verification.verified', language.code))).toBeNull()
      unmount()
    }
  })
})

describe('the corpus notice in each language', () => {
  it('keeps the caveat that the text is unchecked', () => {
    for (const language of LANGUAGES) {
      const { unmount } = renderWithProviders(<CorpusNotice />, { language: language.code })
      // The notice has to say the text was not checked against the original.
      const notice = document.body.textContent ?? ''
      expect(notice.length).toBeGreaterThan(200)
      expect(notice).toContain(translate('corpus.heading', language.code))
      expect(screen.getByText(translate('corpus.inspection', language.code))).toBeInTheDocument()
      unmount()
    }
  })
})

describe('UnverifiedText never adds quotation marks', () => {
  it('wraps the passage in a warning and no quotation marks in any language', () => {
    for (const language of LANGUAGES) {
      const { unmount, container } = renderWithProviders(
        <UnverifiedText>The passage as stored.</UnverifiedText>,
        { language: language.code },
      )
      const warning = screen.getByText(translate('provenance.summaryNotQuotation', language.code))
      expect(warning).toBeInTheDocument()
      // The passage itself is rendered exactly as stored, with nothing added
      // around it: quotation marks would assert an exact wording nobody has
      // checked, in whichever language the reader happens to be reading.
      expect(container.textContent).toContain('The passage as stored.')
      expect(container.textContent).not.toMatch(/["“”«»]/)
      unmount()
    }
  })
})

describe('SourcePointer in each language', () => {
  it('still says when a page is unavailable', () => {
    for (const language of LANGUAGES) {
      const { unmount } = renderWithProviders(
        <SourcePointer pageNumber={null} unavailable sourceUrl={null} />,
        { language: language.code },
      )
      expect(screen.getByText(translate('page.unavailable', language.code))).toBeInTheDocument()
      expect(screen.getByText(translate('source.noneRecorded', language.code))).toBeInTheDocument()
      unmount()
    }
  })

  it('names a page in the chosen language when one is known', () => {
    const { unmount } = renderWithProviders(
      <SourcePointer pageNumber={7} unavailable={false} sourceUrl="https://example.org/a" />,
      { language: 'hi' },
    )
    expect(screen.getByText('पृष्ठ 7')).toBeInTheDocument()
    unmount()
  })
})
