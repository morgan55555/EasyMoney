import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Local dev: Vite proxies API + WebSocket to the backend so the SPA can use
// same-origin calls (no CORS). Production uses the nginx proxy in nginx.conf.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://localhost:8000',
        ws: true,
      },
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.{test.ts,test.tsx}'],
  },
})
