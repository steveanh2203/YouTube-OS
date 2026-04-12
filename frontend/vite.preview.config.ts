import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Separate config for Claude Code preview tool (port 1421)
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  clearScreen: false,
  server: {
    port: 1421,
    strictPort: false,
    watch: {
      ignored: ['**/src-tauri/**'],
    },
  },
})
