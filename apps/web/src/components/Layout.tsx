import { NavLink, Outlet } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api, type ArchiveStats } from '../lib/api'
import { useOffline } from '../lib/offline'
import { formatNumber } from '../lib/format'
import { useLanguage } from '../lib/LanguageContext'
import type { StringKey } from '../lib/i18n'

/**
 * The public navigation is fixed by the project brief and must appear in this
 * order on every page, including on the kiosk build. Two of these sections
 * (Knowledge Graph, Stories) are not implemented in this phase; they are listed
 * anyway and say so, so the navigation is not reshaped as features land.
 *
 * The labels are translation keys rather than English text. The path is the
 * stable identifier; the visible label follows the reader's language, and the
 * path never changes with it, so a link shared in Hindi still opens the same
 * page.
 */
const NAV = [
  { to: '/', key: 'nav.home', end: true },
  { to: '/explore', key: 'nav.explore', end: false },
  { to: '/ai-research', key: 'nav.ai', end: false },
  { to: '/manuscripts', key: 'nav.manuscripts', end: false },
  { to: '/timeline', key: 'nav.timeline', end: false },
  { to: '/knowledge-graph', key: 'nav.graph', end: false },
  { to: '/media', key: 'nav.media', end: false },
  { to: '/stories', key: 'nav.stories', end: false },
] as const satisfies readonly { to: string; key: StringKey; end: boolean }[]

export function Layout() {
  const [stats, setStats] = useState<ArchiveStats | null>(null)
  const { t } = useLanguage()

  useEffect(() => {
    let cancelled = false
    api
      .stats()
      .then((value) => {
        if (!cancelled) setStats(value)
      })
      .catch(() => {
        // The footer count is decoration. If it cannot be loaded, the footer
        // simply omits the number rather than showing a wrong one.
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex min-h-screen flex-col bg-ink-50">
      <OfflineBanner />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm"
      >
        Skip to content
      </a>

      <header className="border-b border-ink-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between">
          <NavLink to="/" className="flex items-baseline gap-2">
            <span className="font-serif text-xl font-semibold text-ink-900">
              {t('site.name')}
            </span>
            <span className="text-xs uppercase tracking-widest text-ink-400">
              {t('site.subtitle')}
            </span>
          </NavLink>
          <nav aria-label="Primary" className="-mx-1 overflow-x-auto">
            <ul className="flex items-center gap-1 whitespace-nowrap px-1">
              {NAV.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      `block rounded-md px-3 py-1.5 text-sm transition ${
                        isActive
                          ? 'bg-indigo-900 font-medium text-white'
                          : 'text-ink-700 hover:bg-ink-100'
                      }`
                    }
                  >
                    {t(item.key)}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        <Outlet />
      </main>

      <footer className="border-t border-ink-200 bg-white">
        <div className="mx-auto max-w-6xl px-4 py-6 text-sm text-ink-600">
          <p className="font-medium text-ink-800">
            Smart India Hackathon 2026 · Problem SIH26096 · Category Hardware
          </p>
          <p className="mt-1 text-xs text-ink-500">
            Reference: sih.decodex.live/sih2026/SIH26096 — Smart Education. Theme:
            Viksit Bharat, Vibrant India, Yuva Shakti, Jai Bharat.
          </p>
          {stats && (
            <p className="mt-2 text-xs text-ink-500">
              {formatNumber(stats.documents_published)} published records ·{' '}
              {formatNumber(stats.chunks)} indexed passages · {formatNumber(stats.graph_nodes)} graph
              nodes
            </p>
          )}
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="max-w-3xl">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-700">
                {t('corpus.heading')}
              </h2>
              <p className="mt-1 text-xs leading-relaxed text-ink-500">{t('corpus.body')}</p>
              <p className="mt-1 text-xs text-ink-500">{t('corpus.originals')}</p>
            </div>
            <LanguagePicker />
          </div>
          <p className="mt-3 max-w-3xl text-xs text-ink-500">{t('language.corpusNote')}</p>
        </div>
      </footer>
    </div>
  )
}

/**
 * The interface language.
 *
 * Deliberately in the footer, next to the provenance note rather than in the
 * header: the notice that this text is unverified is the thing a reader most
 * needs to be able to read in their own language, so the control that changes
 * the language sits beside the text that has to be understood.
 */
function LanguagePicker() {
  const { language, setLanguage, languages, t } = useLanguage()
  return (
    <div className="shrink-0">
      <label
        htmlFor="interface-language"
        className="block text-xs font-medium text-ink-700"
      >
        {t('language.label')}
      </label>
      <select
        id="interface-language"
        value={language}
        onChange={(event) => setLanguage(event.target.value as typeof language)}
        className="mt-1 rounded-md border border-ink-300 bg-white px-2 py-1.5 text-sm"
      >
        {languages.map((option) => (
          <option key={option.code} value={option.code}>
            {option.native} ({option.label})
          </option>
        ))}
      </select>
    </div>
  )
}

/**
 * A standing notice when this device is showing a stored copy.
 *
 * The cache is a copy of what the archive last said, not a fresh answer, so a
 * reader is told rather than left to assume the text is current.
 */
function OfflineBanner() {
  const offline = useOffline()
  const { t } = useLanguage()
  if (!offline) return null
  return (
    <div
      role="status"
      className="bg-amber-100 px-4 py-2 text-center text-sm text-amber-950"
    >
      <strong className="font-semibold">{t('offline.heading')}</strong> {t('offline.body')}
    </div>
  )
}
