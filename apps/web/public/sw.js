/**
 * The offline cache for the kiosk.
 *
 * Written in plain JavaScript on purpose: this file lives in public/ and is
 * served to the browser exactly as written, so a TypeScript annotation here
 * would be a parse error at install time rather than a build error.
 *
 * The rule that shapes this file: an unverified passage and a verified one must
 * not survive an offline reload differently. So the cache is a plain, complete
 * copy of what the server already said, never a re-derivation of it. If the
 * network is gone, this worker shows what the archive last told the truth, and
 * it says plainly that it may be out of date.
 */

// __CACHE_VERSION__ is replaced with a hash of the build output by the build
// step, so a changed worker always starts a new cache. Unreplaced when the file
// is served by the development server, where there is nothing to invalidate.
const VERSION = '__CACHE_VERSION__'

const CACHE_NAME = `dha-shell-${VERSION}`
const DATA_CACHE = `dha-data-${VERSION}`

/** The application shell, needed before anything can be rendered offline. */
const SHELL = ['/', '/index.html', '/favicon.svg', '/offline.html']

/**
 * Read-only archive reads worth keeping.
 *
 * A published document's text does not change silently — a new version produces
 * a new document or an explicit withdrawal — so caching it offline is safe and
 * useful. Everything else stays live.
 */
function isCacheableData(url) {
  if (url.pathname.startsWith('/api/v1/admin')) return false
  if (url.pathname.startsWith('/api/v1/auth')) return false
  if (url.pathname.endsWith('/text')) return true
  if (url.pathname.startsWith('/api/v1/documents/')) return true
  if (url.pathname.startsWith('/api/v1/stats')) return true
  return false
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL))
      // A shell file that cannot be fetched must not block installation: a
      // partially cached archive is more useful than none.
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME && key !== DATA_CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  // Never serve the archivist's pages or data from a cache: a shared kiosk
  // machine must not hand the next visitor a curator's session or working list.
  if (url.pathname.startsWith('/admin') || url.pathname.startsWith('/api/v1/admin')) return

  if (isCacheableData(url)) {
    event.respondWith(staleWhileRevalidate(request, DATA_CACHE))
    return
  }
  event.respondWith(navigationHandler(request))
})

/**
 * Pages: network first, because a stale archive page is misleading, and the
 * cached copy only as a fallback.
 */
async function navigationHandler(request) {
  const cache = await caches.open(CACHE_NAME)
  try {
    const response = await fetch(request)
    if (response.ok) cache.put(request, response.clone())
    return response
  } catch {
    const cached = await cache.match(request)
    if (cached) {
      // Mark it, so the page can tell the reader what they are looking at.
      const headers = new Headers(cached.headers)
      headers.set('X-From-Offline-Cache', '1')
      return new Response(await cached.blob(), {
        status: cached.status,
        statusText: cached.statusText,
        headers,
      })
    }
    const offline = await cache.match('/offline.html')
    if (offline) return offline
    return new Response(
      '<!doctype html><meta charset="utf-8"><title>Offline</title>' +
        '<p>This archive is not available offline yet.</p>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } },
    )
  }
}

/**
 * Archive data: serve the cached copy immediately and refresh in the background,
 * so a reader is never blocked by a slow connection.
 */
async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName)
  const cached = await cache.match(request)
  const network = fetch(request)
    .then((response) => {
      if (response.ok) void cache.put(request, response.clone())
      return response
    })
    .catch(() => undefined)

  if (cached) {
    // Refresh for next time; the reader gets the stored copy now.
    void network
    const headers = new Headers(cached.headers)
    headers.set('X-From-Offline-Cache', '1')
    return new Response(await cached.blob(), {
      status: cached.status,
      statusText: cached.statusText,
      headers,
    })
  }

  const response = await network
  if (response) return response

  // Never invent a record. A miss with no network is reported as a miss, not as
  // an empty archive that looks complete.
  return new Response(
    JSON.stringify({
      detail: 'This record is not stored on this device, and the archive cannot be reached.',
      offline: true,
    }),
    { status: 503, headers: { 'Content-Type': 'application/json' } },
  )
}

/** Lets a page ask the worker to drop everything, for a refresh at source. */
self.addEventListener('message', (event) => {
  if (event.data === 'clear-caches') {
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key)))))
  }
})
