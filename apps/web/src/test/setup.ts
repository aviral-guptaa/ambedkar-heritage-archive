import '@testing-library/jest-dom/vitest'
import { beforeEach } from 'vitest'

/**
 * Restore the storage globals that jsdom implements but Vitest's jsdom
 * environment does not copy onto the test global.
 *
 * jsdom keeps a working `Storage` instance on `window._localStorage`; the
 * `localStorage` property itself arrives as `undefined` on the test global, so
 * anything that remembered a preference in the browser throws under test and
 * would pass in production, or the other way round.
 *
 * jsdom's own implementation is reattached rather than a fake substituted, so
 * the tests still exercise real `Storage` behaviour — including string
 * coercion and the quota — instead of a permissive mock that would let a bug
 * through.
 */
function restoreStorage(name: 'localStorage' | 'sessionStorage'): void {
  const target = globalThis as unknown as Record<string, unknown>
  if (target[name]) return
  const instance = (globalThis as unknown as Record<string, unknown>)[`_${name}`]
  if (instance) {
    Object.defineProperty(target, name, { value: instance, configurable: true, writable: true })
  }
}

restoreStorage('localStorage')
restoreStorage('sessionStorage')

beforeEach(() => {
  // The archive persists a language choice and an admin token. Neither should
  // survive from one test to the next, or a test could pass because of a value
  // an earlier test happened to leave behind.
  localStorage.clear()
  sessionStorage.clear()
})
