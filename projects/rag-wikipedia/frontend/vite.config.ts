import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
    coverage: {
      provider: 'v8',
      // 'text' for the terminal, 'html' to browse uncovered lines, 'lcov' so CI
      // and editor gutter extensions can consume the same run.
      reporter: ['text', 'html', 'lcov'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      // main.tsx is the DOM bootstrap and test-setup.ts is harness wiring —
      // neither has behaviour worth asserting, so counting them would only
      // depress the number without pointing at a real gap.
      exclude: ['src/main.tsx', 'src/test-setup.ts', 'src/**/*.test.{ts,tsx}'],
    },
  },
  server: {
    // `npm run dev` runs Vite on the developer HOST, where the Compose service
    // name `api` does not resolve — the dev proxy must target localhost.
    // `api:8000` belongs only to nginx.conf.template, which runs inside the
    // Compose network. Override with VITE_DEV_PROXY_TARGET if the API is elsewhere.
    proxy: {
      '/query': process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000',
      '/health': process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000',
      '/quality': process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000',
    },
  },
})
