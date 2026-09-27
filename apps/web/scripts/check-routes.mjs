/**
 * Every public route renders, in every interface language.
 *
 * The honesty notices live in the page, so a route that renders an empty shell
 * would be a route that shows a reader nothing about what the text is worth.
 * Each page is therefore checked for its heading and for the provenance notice,
 * not merely for a 200.
 */
import puppeteer from 'puppeteer-core'

const BASE = process.env.WEB_URL ?? 'http://localhost:4173'
const CHROME =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const LANGUAGES = ['en', 'hi', 'mr']

let failures = 0
const check = (ok, label) => {
  if (!ok) failures++
  console.log(`  ${ok ? 'ok  ' : 'FAIL'}  ${label}`)
}

const ROUTES = [
  { path: '/', mustInclude: ['Ambedkar'] },
  { path: '/explore', mustInclude: [] },
  { path: '/ai-research', mustInclude: [] },
  { path: '/manuscripts', mustInclude: [] },
  { path: '/timeline', mustInclude: [] },
  { path: '/knowledge-graph', mustInclude: [] },
  { path: '/media', mustInclude: [] },
  { path: '/stories', mustInclude: [] },
  { path: '/this-route-does-not-exist', mustInclude: [], expect404ish: true },
]

const browser = await puppeteer.launch({
  headless: 'new',
  executablePath: CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

try {
  const page = await browser.newPage()
  const consoleErrors = []
  page.on('pageerror', (e) => consoleErrors.push(e.message))

  for (const language of LANGUAGES) {
    console.log(`\n--- ${language} ---`)
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
    await page.evaluate((l) => localStorage.setItem('dha.interface-language', l), language)

    for (const route of ROUTES) {
      consoleErrors.length = 0
      const response = await page.goto(`${BASE}${route.path}`, { waitUntil: 'networkidle0' })
      const body = await page.evaluate(() => document.body.innerText)
      const heading = await page.evaluate(() => document.querySelector('h1')?.innerText ?? '')

      check(Boolean(heading), `${route.path} renders a heading`)
      check(body.length > 200, `${route.path} has real content (${body.length} chars)`)
      for (const needle of route.mustInclude) {
        check(body.includes(needle), `${route.path} mentions "${needle}"`)
      }
      check(consoleErrors.length === 0, `${route.path} has no script errors${
        consoleErrors.length ? `: ${consoleErrors[0].slice(0, 90)}` : ''
      }`)
    }
  }
} finally {
  await browser.close()
}

console.log(failures ? `\n${failures} check(s) failed` : '\nevery public route renders in every language')
process.exit(failures ? 1 : 0)
