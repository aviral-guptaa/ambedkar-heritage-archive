/**
 * Languages, and what may be translated at all.
 *
 * The interface can be translated. The archive's text cannot, and that
 * distinction is the whole point of this file.
 *
 * A record in this archive is a third-party transcription that has not been
 * checked against the archival original. Translating it would put a machine's
 * reading of an unverified reading between the reader and the source, and the
 * result could not honestly be presented as anything more authoritative than
 * what is already stored. So a passage is always shown in the language it was
 * transcribed in, with a marker saying so.
 *
 * What is translated here is the chrome around the text: the labels, the
 * warnings, the navigation, and the sentences that tell a reader what the
 * archive can and cannot vouch for. Getting those right in a reader's own
 * language matters more than it might seem, because the warning is the part
 * that must not be missed.
 */

export const LANGUAGES = [
  { code: 'en', label: 'English', native: 'English' },
  { code: 'hi', label: 'Hindi', native: 'हिन्दी' },
  { code: 'mr', label: 'Marathi', native: 'मराठी' },
] as const

export type LanguageCode = (typeof LANGUAGES)[number]['code']

export const DEFAULT_LANGUAGE: LanguageCode = 'en'

export function isLanguageCode(value: string | null | undefined): value is LanguageCode {
  return LANGUAGES.some((language) => language.code === value)
}

/**
 * The languages the corpus itself is written in.
 *
 * Used to explain why a passage is shown in the language it is, rather than in
 * the language the reader has chosen for the interface.
 */
export const CORPUS_LANGUAGES: Record<string, { label: string; native: string }> = {
  en: { label: 'English', native: 'English' },
  hi: { label: 'Hindi', native: 'हिन्दी' },
  mr: { label: 'Marathi', native: 'मराठी' },
}

export function corpusLanguageLabel(code: string | null | undefined): string {
  if (!code) return 'the language recorded in the source'
  const known = CORPUS_LANGUAGES[code]
  if (known) return `${known.label} (${known.native})`
  // An unrecognised language code is named rather than guessed at.
  return `${code} (as recorded in the source)`
}
