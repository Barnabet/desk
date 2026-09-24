// Bundles deskd into dist/deskd.mjs with its migrations, the first-party catalog skills and the better-sqlite3 prebuilds
// (N-API: runs under Node and Electron).
import { createHash } from 'node:crypto';
import { cpSync, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const app = fileURLToPath(new URL('..', import.meta.url));
const dist = join(app, 'dist');
rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });

await build({
  entryPoints: [join(app, 'src', 'main.ts')],
  outfile: join(dist, 'deskd.mjs'),
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node22',
  external: ['better-sqlite3', 'bufferutil', 'utf-8-validate'],
  define: { 'process.env.DESK_BUNDLED': '"1"' },
  banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" },
  logLevel: 'warning',
});

const require = createRequire(join(app, '..', '..', 'packages', 'core', 'package.json'));
const sqlite = dirname(realpathSync(require.resolve('better-sqlite3/package.json')));
const target = join(dist, 'node_modules', 'better-sqlite3');
for (const part of ['package.json', 'lib', 'prebuilds']) cpSync(join(sqlite, part), join(target, part), { recursive: true, dereference: true });
cpSync(join(app, '..', '..', 'packages', 'core', 'drizzle'), join(dist, 'drizzle'), { recursive: true });
// First-party catalog skills, installed from disk and checked against their pinned digests (no caches or Finder files).
cpSync(join(app, '..', '..', 'catalog', 'skills'), join(dist, 'catalog', 'skills'), { recursive: true, filter: (src) => !/(^|\/)(__pycache__|\.DS_Store)$|\.pyc$/.test(src) });
// The build id: a hash of the code and migrations. The app compares it with the running daemon's (/v1/health) and
// restarts a daemon from another build even when the version number is the same.
const hash = createHash('sha256').update(readFileSync(join(dist, 'deskd.mjs')));
for (const f of readdirSync(join(dist, 'drizzle'), { recursive: true }).map(String).sort()) if (f.endsWith('.sql') || f.endsWith('.json')) hash.update(f).update(readFileSync(join(dist, 'drizzle', f)));
writeFileSync(join(dist, 'build-id'), hash.digest('hex').slice(0, 16));
console.log(`bundled ${join(dist, 'deskd.mjs')}`);
