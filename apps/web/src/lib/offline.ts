import { useEffect, useState } from 'react'

/**
 * Whether the archive is currently being served from this device.
 *
 * When it is, the reader is told so. An archive that silently shows cached text
 * after the network has gone would let a reader assume they are looking at the
 * current state of a collection, which is not something the cache can promise.
 */
export function useOffline(): boolean {
  const [offline, setOffline] = useState(() =>
    typeof navigator !== 'undefined' ? navigator.onLine === false : false,
  )

  useEffect(() => {
    const goOffline = () => setOffline(true)
    const goOnline = () => setOffline(false)
    window.addEventListener('offline', goOffline)
    window.addEventListener('online', goOnline)
    return () => {
      window.removeEventListener('offline', goOffline)
      window.removeEventListener('online', goOnline)
    }
  }, [])

  return offline
}

/**
 * Install the offline worker.
 *
 * Registration is deliberately not awaited before the page renders: a reader
 * should never wait on a service worker to see the archive. It is also skipped
 * entirely in development, where a cached shell would hide the changes being
 * made and there is nothing to be offline for.
 */
export function registerServiceWorker(): void {
  if (import.meta.env.DEV) return
  if (!('serviceWorker' in navigator)) return

  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // A browser that refuses to register still works; it just has no offline
      // copy, and saying so is better than pretending otherwise.
      console.info('The offline cache is unavailable in this browser.')
    })
  })
}
