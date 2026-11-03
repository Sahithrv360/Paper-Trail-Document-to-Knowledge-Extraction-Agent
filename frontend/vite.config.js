import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.PAPERTRAIL_API_TARGET || 'http://127.0.0.1:8765',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            // The review API only accepts same-origin requests; the Vite proxy
            // keeps the browser token private and presents a loopback origin.
            proxyReq.setHeader('Origin', process.env.PAPERTRAIL_API_TARGET || 'http://127.0.0.1:8765');
          });
        }
      }
    }
  }
})
