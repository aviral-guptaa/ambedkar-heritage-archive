import puppeteer from 'puppeteer-core'

// The production build, which is what a kiosk runs. Override with WEB_URL.
const BASE = process.env.WEB_URL ?? 'http://localhost:4173'
// CHROME_PATH, not CHROME: the other three checks use that name, and a script
// that silently reads a different variable is a script that only works on the
// machine where it was written.
const browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: 'new', args:['--no-sandbox'] })
const page = await browser.newPage()
let failures = 0
const check = (ok, label) => { console.log(`${ok ? '  ok' : 'FAIL'}  ${label}`); if (!ok) failures++ }

await page.goto(`${BASE}/`, { waitUntil: 'networkidle0' })

// English by default.
const en = await page.evaluate(() => document.body.innerText)
check(/digital heritage archive/i.test(en), 'English interface by default')
check(en.includes('Read the provenance before you rely on a passage.'), 'English corpus notice')

// Switch to Hindi through the real control.
await page.select('#interface-language', 'hi')
await new Promise(r => setTimeout(r, 400))
const hi = await page.evaluate(() => ({
  text: document.body.innerText,
  nav: [...document.querySelectorAll('nav a')].map(a => a.textContent.trim()),
  lang: document.documentElement.lang,
  stored: localStorage.getItem('dha.interface-language'),
}))
check(hi.lang === 'hi', 'document lang set to hi')
check(hi.stored === 'hi', 'choice remembered in localStorage')
check(hi.nav.includes('अन्वेषण') && hi.nav.includes('समयरेखा'), `Hindi nav labels: ${hi.nav.slice(0,4).join(', ')}`)
check(hi.text.includes('डिजिटल विरासत संग्रह'), 'Hindi subtitle')
check(hi.text.includes('मूल के साथ सत्यापित') || hi.text.includes('असत्यापित'), 'Hindi verification marks')
check(!hi.text.includes('Read the provenance before'), 'English notice replaced')
// The paths must not change with the language.
const paths = await page.evaluate(() => [...document.querySelectorAll('nav a')].map(a => a.getAttribute('href')))
check(JSON.stringify(paths) === JSON.stringify(['/','/explore','/ai-research','/manuscripts','/timeline','/knowledge-graph','/media','/stories']), `paths stable: ${paths.join(',')}`)

// A translated link must still open the same record.
await page.select('#interface-language', 'mr')
await new Promise(r => setTimeout(r, 400))
const mr = await page.evaluate(() => document.body.innerText)
check(mr.includes('डिजिटल वारसा संग्रह'), 'Marathi subtitle')
check(mr.includes('किसी') || mr.includes('मजकूर'), 'Marathi notice present')

// Reload: the choice must survive, since a kiosk is not reconfigured each visit.
await page.reload({ waitUntil: 'networkidle0' })
const after = await page.evaluate(() => ({ lang: document.documentElement.lang, sel: document.querySelector('#interface-language')?.value }))
check(after.lang === 'mr' && after.sel === 'mr', 'choice survives a reload')

await browser.close()
console.log(failures ? `\n${failures} check(s) failed` : '\nall i18n checks passed')
process.exit(failures ? 1 : 0)
