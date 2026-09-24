import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { buildMainAndPreload } from './build.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
await buildMainAndPreload();
const server = await createServer({ configFile: join(root, 'vite.config.ts') });
await server.listen();
const url = server.resolvedUrls?.local[0] ?? 'http://localhost:5173/';
const { default: electronPath } = await import('electron');
const child = spawn(electronPath, [root], { stdio: 'inherit', env: { ...process.env, DESK_RENDERER_URL: url } });
child.on('exit', async (code) => {
  await server.close();
  process.exit(code ?? 0);
});
