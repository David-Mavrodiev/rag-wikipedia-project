import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
  },
  server: {
    // `npm run dev` runs Vite on the developer HOST, where the Compose service
    // name `api` does not resolve — the dev proxy must target localhost.
    // `api:8000` belongs only to nginx.conf.template, which runs inside the
    // Compose network. Override with VITE_DEV_PROXY_TARGET if the API is elsewhere.
    proxy: {
      '/query': process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000',
      '/health': process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000',
    },
  },
})
