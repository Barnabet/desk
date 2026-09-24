import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('./src/renderer', import.meta.url)),
  base: './',
  plugins: [react()],
  // One local bundle loaded from disk: chunk size does not matter for load time here.
  build: { outDir: fileURLToPath(new URL('./dist/renderer', import.meta.url)), emptyOutDir: true, sourcemap: true, chunkSizeWarningLimit: 2000 },
  server: { port: 5173, strictPort: true },
});
