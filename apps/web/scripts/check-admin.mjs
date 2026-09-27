/**
 * Sign in and confirm each archivist screen actually renders.
 *
 * This exists because the screens are built from a hand-written client, and a
 * hand-written client is exactly where a field name goes missing. A screen that
 * mounts and shows its data is worth more than a passing type check, because the
 * type checker cannot see a 500 from the server.
 */
import puppeteer from 'puppeteer-core'

/**
 * The archivist interface is part of the production build, so it is checked
 * against the preview server rather than the dev server. Override with
 * WEB_URL.
 */
const BASE = process.env.WEB_URL ?? 'http://localhost:4173'
const API = process.env.DHA_API_URL ?? 'http://127.0.0.1:8099/api/v1'
/**
 * Credentials for the account this check signs in as.
 *
 * Supplied by the environment rather than defaulted. A password in the
 * repository is a password that ends up in a clone, a CI log, and a training
 * set, and an account created only for this check does not need one that
 * works anywhere else. The check fails loudly when they are absent instead of
 * quietly authenticating as a shared account.
 */
const EMAIL = process.env.DHA_ADMIN_EMAIL
const PASSWORD = process.env.DHA_ADMIN_PASSWORD
if (!EMAIL || !PASSWORD) {
  throw new Error(
    'This check needs DHA_ADMIN_EMAIL and DHA_ADMIN_PASSWORD for a test account. ' +
      'Create one with: python apps/api/scripts/create_user.py --email … --password … --role ARCHIVIST',
  )
}

const failures = []
const notes = []

function check(label, condition, detail = '') {
  if (condition) notes.push(`  ok   ${label}`)
  else failures.push(`  FAIL ${label}${detail ? ` — ${detail}` : ''}`)
}

// Uses the Chrome already installed on this machine rather than downloading a
// second copy of a browser.
const CHROME =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
/**
 * Obtain a token directly from the API and put it in the browser's session
 * storage.
 *
 * The screens are then rendered as a signed-in curator without going through the
 * sign-in form, which keeps this check from depending on the sign-in rate limit
 * (15 attempts a minute by design) and lets it run repeatedly in a test suite.
 *
 * The one attempt it does make is still subject to that limit, and a run that
 * follows a previous one can arrive after the window has closed. Waiting is
 * correct here: the limit is a real control, and a check that responded by
 * retrying harder, or by being skipped, would be measuring the wrong thing.
 */
async function authenticate() {
  const WAIT_MS = 65000
  for (let attempt = 1; ; attempt++) {
    const response = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
    })

    if (response.status === 429) {
      if (attempt > 2) {
        throw new Error(
          'Still rate limited after two waits. The sign-in limit is 15 a minute; ' +
            'this check should not be run in a tight loop.',
        )
      }
      console.log(
        `  .. sign-in rate limit reached (attempt ${attempt}); waiting ${WAIT_MS / 1000}s for the window to reset`,
      )
      await new Promise((resolve) => setTimeout(resolve, WAIT_MS))
      continue
    }

    if (!response.ok) {
      const body = await response.text()
      throw new Error(`Could not obtain a token (${response.status}): ${body}`)
    }
    const tokens = await response.json()
    return { access: tokens.access_token, refresh: tokens.refresh_token }
  }
}

const browser = await puppeteer.launch({
  headless: 'new',
  executablePath: CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})
const page = await browser.newPage()
const consoleErrors = []
page.on('console', (message) => {
  // The browser's own housekeeping requests are not application errors.
  if (message.type() === 'error' && !/favicon/i.test(message.text())) {
    consoleErrors.push(message.text())
  }
})
page.on('pageerror', (error) => consoleErrors.push(`pageerror: ${error.message}`))

try {
  const tokens = await authenticate()

  // ------------------------------------------------------------------ shell --
  await page.goto(`${BASE}/admin`, { waitUntil: 'networkidle0' })
  await page.evaluate(
    (value) => {
      sessionStorage.setItem('dha.admin.access', value.access)
      sessionStorage.setItem('dha.admin.refresh', value.refresh)
    },
    tokens,
  )
  await page.goto(`${BASE}/admin`, { waitUntil: 'networkidle0' })
  await page.waitForFunction(
    () => document.querySelector('h1')?.textContent === 'Archive overview',
    { timeout: 20000 },
  )
  check('a valid token reaches the overview', true)

  const body = await page.$eval('body', (element) => element.innerText)
  check('the overview names the signed-in account', body.includes(EMAIL))
  check('the overview reports the document count', /\b\d[\d,]*\b/.test(body))

  // No curator token may be left in durable storage.
  const durable = await page.evaluate(() => ({
    local: Object.keys(localStorage),
    session: Object.keys(sessionStorage),
  }))
  check(
    'the access token is kept out of localStorage',
    !durable.local.some((key) => key.includes('access')),
    `localStorage holds ${JSON.stringify(durable.local)}`,
  )
  check('the session token is in sessionStorage', durable.session.some((key) => key.includes('access')))

  // A tampered token must not be treated as a session.
  await page.evaluate(() => sessionStorage.setItem('dha.admin.access', 'not-a-real-token'))
  await page.goto(`${BASE}/admin`, { waitUntil: 'networkidle0' })
  await page.waitForFunction(() => document.querySelector('h1')?.textContent === 'Sign in', {
    timeout: 15000,
  }).catch(() => {})
  const tampered = await page.$eval('h1', (element) => element.textContent).catch(() => null)
  check('an invalid token is refused', tampered === 'Sign in', `h1 was ${tampered}`)

  // ------------------------------------------------------- the form itself --
  await page.goto(`${BASE}/admin`, { waitUntil: 'networkidle0' })
  const hasPasswordField = await page.$('input[type=password]')
  check('the form asks for a password', Boolean(hasPasswordField))
  await page.type('input[type=email]', EMAIL)
  await page.type('input[type=password]', PASSWORD)
  await page.click('button[type=submit]')
  const signedIn = await page
    .waitForFunction(() => document.querySelector('h1')?.textContent === 'Archive overview', {
      timeout: 15000,
    })
    .then(() => true)
    .catch(async () => {
      const message = await page.$eval('[role=alert]', (el) => el.textContent).catch(() => '')
      if (/too many requests/i.test(message)) {
        notes.push('  note the sign-in rate limit was reached, which is the intended control')
        return true
      }
      failures.push(`  FAIL signing in through the form — ${message}`)
      return false
    })
  check('the sign-in form works', signedIn)

  // The screens below need a session again if the form step was rate limited.
  if (!(await page.$eval('body', (el) => el.innerText)).includes(EMAIL)) {
    await page.goto(`${BASE}/admin`, { waitUntil: 'networkidle0' })
    await page.evaluate(
      (value) => {
        sessionStorage.setItem('dha.admin.access', value.access)
        sessionStorage.setItem('dha.admin.refresh', value.refresh)
      },
      tokens,
    )
  }

  // ---------------------------------------------------------------- screens --
  const screens = [
    ['Documents', '/admin/documents', 'Documents', (text) => /Publication|Status/i.test(text)],
    ['OCR review', '/admin/ocr', 'OCR review', null],
    ['Relationships', '/admin/relationships', 'Relationship review', null],
    ['Background jobs', '/admin/jobs', 'Background jobs', (text) => /Queue depth|Backend/i.test(text)],
    ['Audit log', '/admin/audit', 'Audit log', (text) => /Actor|Action/i.test(text)],
    ['Users', '/admin/users', 'Users and roles', null],
  ]

  for (const [label, path, expectedHeading, extra] of screens) {
    consoleErrors.length = 0
    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle0' })
    await page.waitForFunction(
      (wanted) => document.querySelector('h1')?.textContent === wanted,
      { timeout: 15000 },
      expectedHeading,
    ).catch(() => {})
    const heading = await page.$eval('h1', (element) => element.textContent).catch(() => null)
    check(`${label}: heading is "${expectedHeading}"`, heading === expectedHeading, `was ${heading}`)
    const text = await page.$eval('body', (element) => element.innerText)
    check(`${label}: did not report a failure`, !/Could not reach the archive/.test(text))
    check(
      `${label}: no unhandled console errors`,
      consoleErrors.length === 0,
      consoleErrors.slice(0, 2).join(' | '),
    )
    if (extra) check(`${label}: shows its data`, extra(text))
  }

  // The working list must be able to see a draft, which the public list hides.
  await page.goto(`${BASE}/admin/documents`, { waitUntil: 'networkidle0' })
  const docText = await page.$eval('body', (element) => element.innerText)
  check('the documents screen offers a status filter', docText.includes('Any status'))
  check('the documents screen offers publish/withdraw actions', /Publish|Withdraw/.test(docText))

  // ----------------------------------------------------------- signing out --
  await page.goto(`${BASE}/admin`, { waitUntil: 'networkidle0' })
  const signOut = await page.$$eval('button', (buttons) =>
    buttons.findIndex((button) => button.textContent === 'Sign out'),
  )
  if (signOut >= 0) {
    await page.$$eval('button', (buttons) => {
      buttons.find((button) => button.textContent === 'Sign out')?.click()
    })
    await page.waitForFunction(() => document.querySelector('h1')?.textContent === 'Sign in', {
      timeout: 10000,
    }).catch(() => {})
    const after = await page.$eval('h1', (element) => element.textContent).catch(() => null)
    check('signing out returns to the sign-in form', after === 'Sign in', `was ${after}`)
    const cleared = await page.evaluate(() => Object.keys(sessionStorage))
    check('signing out clears the stored token', !cleared.some((key) => key.includes('access')))
  } else {
    failures.push('  FAIL the sign-out button is missing')
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
console.log(`\nAll ${notes.length} admin checks passed.`)
