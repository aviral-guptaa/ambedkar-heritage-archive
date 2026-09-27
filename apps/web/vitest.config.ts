/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // jsdom's default document lives on an opaque `about:blank` origin, which
    // has no localStorage. The archive stores the reader's language and the
    // admin session there, so the tests run on an origin that has storage —
    // which is also what a browser actually gives the app.
    environmentOptions: { jsdom: { url: 'http://localhost:4173/' } },
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
