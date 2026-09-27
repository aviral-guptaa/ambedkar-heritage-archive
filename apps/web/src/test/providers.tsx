import { render, type RenderOptions, type RenderResult } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactElement, ReactNode } from 'react'
import { LanguageProvider } from '../lib/LanguageContext'
import { isLanguageCode, type LanguageCode } from '../lib/languages'

export function clearStoredLanguage(): void {
  localStorage.removeItem('dha.interface-language')
}

/**
 * Render with the providers the archive's components expect.
 *
 * The language provider is not optional decoration: a component that reads a
 * translation throws rather than silently falling back to English, so a test
 * that omitted it would fail for a reason unrelated to what it was checking.
 *
 * The `language` option is checked rather than coerced. `setItem` would happily
 * store an object as `"[object Object]"`, the lookup would reject it, and the
 * test would quietly assert the English interface while claiming to check the
 * Marathi one — a test that passes and proves nothing.
 */
export function renderWithProviders(
  ui: ReactElement,
  options?: RenderOptions & { language?: LanguageCode },
): RenderResult {
  const { language, ...rest } = options ?? {}
  if (language !== undefined) {
    if (!isLanguageCode(language)) {
      throw new Error(
        `renderWithProviders expects a language code such as 'en' or 'mr', received ${JSON.stringify(language)}. ` +
          'Pass LANGUAGES[i].code, not the entry itself.',
      )
    }
    localStorage.setItem('dha.interface-language', language)
  }
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <LanguageProvider>
      <MemoryRouter>{children}</MemoryRouter>
    </LanguageProvider>
  )
  return render(ui, { wrapper: Wrapper, ...rest })
}
