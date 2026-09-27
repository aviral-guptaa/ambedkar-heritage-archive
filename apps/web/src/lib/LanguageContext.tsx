import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { DEFAULT_LANGUAGE, LANGUAGES, type LanguageCode } from './languages'
import { readLanguage, storeLanguage, translate, type StringKey } from './i18n'

/**
 * The reader's chosen language for the interface.
 *
 * This changes labels, navigation and the provenance warnings. It deliberately
 * does not change any archival text: a passage is shown in the language it was
 * transcribed in, because a translation of an unverified transcription would
 * add a second layer of doubt to text that is already marked unverified.
 */
interface LanguageContext {
  language: LanguageCode
  setLanguage: (language: LanguageCode) => void
  t: (key: StringKey) => string
  languages: typeof LANGUAGES
}

const Context = createContext<LanguageContext | null>(null)

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<LanguageCode>(readLanguage)

  useEffect(() => {
    storeLanguage(language)
  }, [language])

  const setLanguage = useCallback((next: LanguageCode) => setLanguageState(next), [])

  const value = useMemo<LanguageContext>(
    () => ({
      language,
      setLanguage,
      // Bound here so a component does not have to pass the language around.
      t: (key: StringKey) => translate(key, language),
      languages: LANGUAGES,
    }),
    [language, setLanguage],
  )

  return <Context.Provider value={value}>{children}</Context.Provider>
}

export function useLanguage(): LanguageContext {
  const context = useContext(Context)
  if (!context) throw new Error('useLanguage must be used inside LanguageProvider')
  return context
}

export { DEFAULT_LANGUAGE }
export type { LanguageCode }
