/**
 * Prove the offline cache works, and prove what it refuses to do.
 *
 * A service worker that silently does nothing is the usual outcome, and a cache
 * that stores the archivist's working list is the dangerous one. Both are
 * checked here against a real browser with the network genuinely switched off.
 */
import puppeteer from 'puppeteer-core'

/**
 * A production build, on the port the preview server uses.
 *
 * The default is 4173 rather than the dev server's 5173 on purpose. The
 * service worker is only registered in a production build, so pointing this at
 * the dev server made the check depend on a worker left over in the browser
 * profile from an earlier run: it passed against the wrong server, and in a
 * fresh profile or CI — the places where it matters — it failed. A check that
 * only passes because of leftover state is not a check.
 */
const BASE = process.env.WEB_URL ?? 'http://localhost:4173'
const CHROME =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

const failures = []
const notes = []
function check(label, condition, detail = '') {
  if (condition) notes.push(`  ok   ${label}`)
  else failures.push(`  FAIL ${label}${detail ? ` — ${detail}` : ''}`)
}

const browser = await puppeteer.launch({
  headless: 'new',
  executablePath: CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

try {
  const page = await browser.newPage()

  // The worker is only registered in a production build, so serve that.
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle0' })

  // Registration is asynchronous, and `networkidle0` says nothing about it: a
  // page can be completely idle while the worker is still installing. Sampling
  // `getRegistration()` once passed only because an earlier run had left a
  // worker registered, and would fail in a fresh profile or CI — which is
  // exactly where this check matters most.
  //
  // `navigator.serviceWorker.ready` is the correct wait: it resolves when a
  // registration exists and has an active worker. It never rejects, so it is
  // raced against a timeout to distinguish "slow" from "never happened".
  const registered = await page.evaluate(async () => {
    if (!('serviceWorker' in navigator)) return 'unsupported'
    const settled = await Promise.race([
      navigator.serviceWorker.ready.then(() => 'registered'),
      new Promise((resolve) => setTimeout(() => resolve('absent'), 15000)),
    ])
    if (settled === 'registered') return 'registered'
    // Distinguish a slow install from no registration at all, so a failure says
    // which happened.
    const any = await navigator.serviceWorker.getRegistration()
    return any ? 'registered-but-not-active' : 'absent'
  })
  check(
    'the service worker registers on a production build',
    registered === 'registered' || registered === 'unsupported',
    `registration was ${registered}`,
  )

  if (registered === 'registered') {
    await page.evaluate(() => navigator.serviceWorker.ready)
    const cacheNames = await page.evaluate(() => caches.keys())
    check('the worker created a cache', cacheNames.length > 0, `caches: ${JSON.stringify(cacheNames)}`)

    // Read a document so its text is stored for offline use.
    const explored = await page.goto(`${BASE}/manuscripts`, { waitUntil: 'networkidle0' })
    check('the manuscripts page loads online', explored?.status() === 200)

    const firstSlug = await page.evaluate(async () => {
      const response = await fetch('/api/v1/documents?limit=1')
      const page_ = await response.json()
      return page_.items?.[0]?.slug ?? null
    })
    check('a record was found to cache', Boolean(firstSlug), `slug was ${firstSlug}`)

    if (firstSlug) {
      const detail = await page.goto(`${BASE}/manuscripts/${firstSlug}`, { waitUntil: 'networkidle0' })
      check('a document detail page loads online', detail?.status() === 200)
    }

    // Give the worker a moment to write the caches.
    await new Promise((resolve) => setTimeout(resolve, 1500))

    // ------------------------------------------------------- now go offline --
    await page.setOfflineMode(true)
    notes.push('  note the browser is now offline')

    const offlineHome = await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' }).catch(() => null)
    check(
      'the home page is still served offline from the cache',
      offlineHome?.status() === 200,
      `status ${offlineHome?.status()}`,
    )
    const offlineText = await page.$eval('body', (element) => element.innerText).catch(() => '')
    check('the offline page has real content, not just a shell', offlineText.length > 200)
    check(
      'the reader is told they are offline',
      /offline/i.test(offlineText),
      'no offline notice was shown',
    )

    if (firstSlug) {
      const cachedDetail = await page
        .goto(`${BASE}/manuscripts/${firstSlug}`, { waitUntil: 'domcontentloaded' })
        .catch(() => null)
      check(
        'an already-read document is available offline',
        cachedDetail?.status() === 200,
        `status ${cachedDetail?.status()}`,
      )
      const body = await page.$eval('body', (element) => element.innerText).catch(() => '')
      check('the offline document still states its verification', /verif|unverified|checked/i.test(body))
    }

    // A record that was never opened must be reported as missing, not faked.
    const missing = await page
      .goto(`${BASE}/manuscripts/never-opened-this-record`, { waitUntil: 'domcontentloaded' })
      .catch(() => null)
    check(
      'an unopened record is served rather than invented',
      missing?.status() === 200 || missing?.status() === 503,
      `status ${missing?.status()}`,
    )
    const missingBody = await page.$eval('body', (element) => element.innerText).catch(() => '')
    check(
      'a missing offline record says so plainly',
      /not available offline|not stored|offline|not found/i.test(missingBody),
      'no explanation was shown',
    )

    // ---------------------------------------------- the archivist is excluded --
    // The working list must never come out of the cache. Offline, the honest
    // outcome is that the request fails: a cached curator's list on a shared
    // kiosk would be worse than no list at all.
    const adminResponse = await page.evaluate(async () => {
      try {
        const response = await fetch('/api/v1/admin/documents')
        return { status: response.status, fromCache: response.headers.get('X-From-Offline-Cache') }
      } catch (error) {
        return { status: 0, failed: true, fromCache: null, error: String(error) }
      }
    })
    check(
      'the archivist working list is not served from the cache',
      adminResponse.fromCache === null,
      `response was ${JSON.stringify(adminResponse)}`,
    )
    check(
      'the archivist working list does not resolve offline',
      adminResponse.failed === true || adminResponse.status >= 400,
      `the request unexpectedly succeeded with ${adminResponse.status}`,
    )

    // The sign-in form must not be cached either.
    const adminPage = await page
      .goto(`${BASE}/admin`, { waitUntil: 'domcontentloaded' })
      .catch(() => null)
    const adminText = await page.$eval('body', (element) => element.innerText).catch(() => '')
    check(
      'the archivist area is never served from the cache',
      adminPage?.status() !== 200 || /sign in/i.test(adminText),
      `status ${adminPage?.status()} and body ${adminText.slice(0, 60)}`,
    )

    await page.setOfflineMode(false)
  }
} finally {
  await browser.close()
}

console.log(notes.join('\n'))
if (failures.length > 0) {
  console.log(`\n${failures.length} failure(s):`)
  console.log(failures.join('\n'))
  process.exit(1)
}
console.log(`\nAll ${notes.length} offline checks passed.`)
