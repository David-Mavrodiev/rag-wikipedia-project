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
    proxy: {
      '/query': 'http://api:8000',
      '/health': 'http://api:8000',
    },
  },
})
