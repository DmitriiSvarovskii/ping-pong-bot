import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { randomUUID } from 'node:crypto'
import { defineConfig } from 'vitest/config'

const buildId = `${Date.now().toString(36)}-${randomUUID()}`

export default defineConfig({
  define: {
    __APP_BUILD_ID__: JSON.stringify(buildId),
  },
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'pingstats-version-manifest',
      generateBundle() {
        this.emitFile({
          type: 'asset',
          fileName: 'version.json',
          source: JSON.stringify({ build_id: buildId }),
        })
      },
    },
  ],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: true,
  },
})
