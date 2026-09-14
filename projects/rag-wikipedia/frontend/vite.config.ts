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
      coverage: {
        provider: 'v8',
        // 'text' for the terminal, 'html' to browse the uncovered lines, 'lcov'
        // so CI and editor gutter extensions consume the same run.
        reporter: ['text', 'html', 'lcov'],
        reportsDirectory: './coverage',
        include: ['src/**/*.{ts,tsx}'],
        // `all` keeps files the tests never import in the report at 0% rather
        // than dropping them. App.tsx is why this matters: it is untested, and
        // the default of reporting only imported files would have hidden that
        // behind a components-only number in the nineties.
        all: true,
        // main.tsx is the DOM bootstrap and test-setup.ts is harness wiring.
        // Neither holds behaviour worth asserting, so counting them would
        // depress the number without pointing at a real gap. Nothing else is
        // excluded - App.tsx stays in precisely because it is the gap.
        exclude: ['src/main.tsx', 'src/test-setup.ts', 'src/**/*.test.{ts,tsx}'],
        // The floor, enforced by `npm run test:coverage` and by the same
        // command in CI. Measured at the values below when the gate was
        // introduced, rounded down; ratchet them UP as coverage improves,
        // never down to make a red build green.
        thresholds: {
          statements: 76,
          branches: 84,
          functions: 92,
          lines: 76,
        },
      },
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
