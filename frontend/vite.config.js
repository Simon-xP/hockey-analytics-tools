import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      // The build log page imports docs/how-i-made-this.md from the repo root,
      // which sits outside this Vite root.
      allow: ['..'],
    },
  },
})
