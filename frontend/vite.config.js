import { defineConfig } from 'vite';

export default defineConfig({
  server: { proxy: { '/api': 'http://127.0.0.1:8501' } },
  build: { sourcemap: false },
});
