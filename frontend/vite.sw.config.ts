import { defineConfig } from 'vite'

// Second build (AGENTS.md §2.14): the Web Push service worker. A separate,
// React-free bundle emitted as an unhashed `dist/sw.js` so the browser can
// register it by a fixed URL. It reuses the shared text helpers (lib/
// notificationText) and locale dictionaries (i18n/core) — no React in the
// module graph. `emptyOutDir: false` keeps the main build's dist/ intact and
// only adds sw.js alongside it.
export default defineConfig({
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    target: 'es2020',
    rollupOptions: {
      input: { sw: 'src/sw/sw.ts' },
      output: {
        // Fixed, unhashed name: the SW is registered at /sw.js.
        entryFileNames: 'sw.js',
      },
    },
  },
})
