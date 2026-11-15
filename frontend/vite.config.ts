import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  build: { target: 'es2022' },
  server: {
    proxy: { '/api': { target: loadEnv(mode, '.', 'ATLAS_').ATLAS_API_URL || 'http://127.0.0.1:8000', changeOrigin: true } },
  },
}));
