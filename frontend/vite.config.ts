import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'

// basicSsl serves the dev server over HTTPS with a self-signed certificate.
// Mobile browsers block camera + high-accuracy GPS on plain http://SERVER_IP,
// so this is required to test from a phone in development (accept the
// certificate warning once). The /api proxy keeps API calls same-origin.
export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
