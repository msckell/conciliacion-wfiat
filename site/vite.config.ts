import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The page reads data/site/site.json from the repo root at build time.
  server: { fs: { allow: ['..'] } },
})
