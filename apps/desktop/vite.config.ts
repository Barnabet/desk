import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('./src/renderer', import.meta.url)),
  base: './',
  plugins: [react()],
  build: { outDir: fileURLToPath(new URL('./dist/renderer', import.meta.url)), emptyOutDir: true, sourcemap: true },
  server: { port: 5173, strictPort: true },
});
