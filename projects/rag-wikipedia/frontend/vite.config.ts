import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // loadEnv, not process.env: Vite does not put .env files on process.env, so
  // VITE_DEV_PROXY_TARGET set in .env.local was silently ignored and the proxy
  // always fell back to localhost.
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000'

  return {
    plugins: [react()],
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: './src/test-setup.ts',
    },
    server: {
      // `npm run dev` runs Vite on the developer HOST, where the Compose service
      // name `api` does not resolve - the dev proxy must target localhost.
      // `api:8000` belongs only to nginx.conf.template, which runs inside the
      // Compose network.
      proxy: {
        '/query': target,
        '/health': target,
        '/quality': target,
      },
    },
  }
})
