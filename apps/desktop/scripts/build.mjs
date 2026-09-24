import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('..', import.meta.url));

/** Main and preload: CommonJS bundles (Electron's sandboxed preload must be one file). */
export async function buildMainAndPreload() {
  const common = { bundle: true, platform: 'node', format: 'cjs', target: 'node22', external: ['electron'], sourcemap: true, logLevel: 'warning' };
  await Promise.all([
    build({ ...common, entryPoints: [join(root, 'src/main/index.ts')], outfile: join(root, 'dist/main.cjs') }),
    build({ ...common, entryPoints: [join(root, 'src/preload/index.ts')], outfile: join(root, 'dist/preload.cjs') }),
  ]);
}

export async function buildRenderer() {
  const { build: viteBuild } = await import('vite');
  await viteBuild({ configFile: join(root, 'vite.config.ts'), logLevel: 'warn' });
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  await buildMainAndPreload();
  await buildRenderer();
  console.log(`built ${join(root, 'dist')}`);
}
