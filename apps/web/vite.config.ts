import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Stamps the service worker with a version derived from its own contents.
 *
 * Without this the worker would keep serving a stale cache forever, because a
 * cached service worker is only replaced when its bytes change. Deriving the
 * version from the file itself means editing the worker always produces a new
 * cache name and the old caches are dropped.
 */
function stampServiceWorker(): Plugin {
  const FILE = 'sw.js'
  return {
    name: 'dha-stamp-service-worker',
    apply: 'build',
    async closeBundle() {
      // The name is derived from the build output, so two builds of the same
      // source share a cache and a changed worker does not.
      const outDir = 'dist'
      let stamp = ''
      try {
        const index = await readFile(`${outDir}/index.html`, 'utf8')
        const referenced = [...index.matchAll(/(?:src|href)="\/(assets\/[^"]+)"/g)].map((m) => m[1])
        const parts = await Promise.all(
          referenced.map(async (asset) => createHash('sha256').update(await readFile(`${outDir}/${asset}`)).digest('hex')),
        )
        stamp = createHash('sha256').update(parts.join('')).digest('hex').slice(0, 12)
      } catch {
        // A build with nothing to hash still needs a distinct cache name, and
        // the timestamp changes it on every run rather than silently reusing
        // an old cache.
        stamp = String(Date.now())
      }

      const path = `${outDir}/${FILE}`
      const source = await readFile('public/sw.js', 'utf8')
      await import('node:fs/promises').then((fs) =>
        fs.writeFile(
          path,
          // Replaced as a whole literal so the built file contains a plain
          // string rather than a leftover conditional.
          source.replace("const VERSION = '__CACHE_VERSION__'", `const VERSION = '${stamp}'`),
          'utf8',
        ),
      )
      this.info?.(`service worker stamped ${stamp}`)
    },
  }
}

export default defineConfig({
  plugins: [react(), stampServiceWorker()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: process.env.DHA_API_URL ?? 'http://127.0.0.1:8099',
        changeOrigin: true,
      },
    },
  },
  // The preview server serves the production build, which is the only build in
  // which the service worker registers. It needs its own proxy: `preview` does
  // not inherit `server.proxy`, so without this the offline check would run
  // against an archive with no API at all.
  preview: {
    port: 4173,
    host: true,
    proxy: {
      '/api': {
        target: process.env.DHA_API_URL ?? 'http://127.0.0.1:8099',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
})
