import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In Docker the app is served by nginx, which reverse-proxies /api/* to the
// orchestrator, so the browser only ever talks to one origin (no CORS needed).
// For local `npm run dev`, Vite proxies /api to the orchestrator on :8000.
const ORCHESTRATOR = process.env.ORCHESTRATOR_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: ORCHESTRATOR,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})