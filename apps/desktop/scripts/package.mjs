// Packages Desk for macOS: builds the app and the bundled deskd, lays out Desk.app with electron-builder,
// signs it ad hoc (no Apple identity; Apple silicon refuses unsigned code), then writes release/Desk-<v>-<arch>.dmg and .zip.
// The Windows target is configured (`--win`) but untested: deskd itself is macOS-only for now.
import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import builder from 'electron-builder';
import { buildMainAndPreload, buildRenderer } from './build.mjs';
import { installUv } from './uv.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const repo = join(root, '..', '..');
const release = join(root, 'release');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const require = createRequire(import.meta.url);
const electronPkg = JSON.parse(readFileSync(require.resolve('electron/package.json'), 'utf8'));
const win = process.argv.includes('--win');
const arch = process.arch === 'arm64' ? 'arm64' : 'x64';

const run = (file, args, opts = {}) => execFileSync(file, args, { stdio: 'inherit', ...opts });

rmSync(release, { recursive: true, force: true });
await buildMainAndPreload();
await buildRenderer();
run(process.execPath, [join(repo, 'apps', 'daemon', 'scripts', 'bundle.mjs')]);

// The staged app has no dependencies: main, preload and renderer are bundled. It is staged outside the
// workspace so electron-builder does not fall back to collecting apps/desktop's node_modules.
const stage = mkdtempSync(join(tmpdir(), 'desk-stage-'));
for (const f of ['main.cjs', 'preload.cjs', 'renderer']) cpSync(join(root, 'dist', f), join(stage, 'dist', f), { recursive: true });
// An empty npm lockfile tells electron-builder there is nothing to collect.
writeFileSync(join(stage, 'package-lock.json'), JSON.stringify({ name: 'desk', version: pkg.version, lockfileVersion: 3, requires: true, packages: { '': { name: 'desk', version: pkg.version } } }));
writeFileSync(
  join(stage, 'package.json'),
  JSON.stringify({ name: 'desk', productName: 'Desk', version: pkg.version, description: 'Desk: a coordinator and parallel threads for your projects.', author: 'Desk', main: 'dist/main.cjs' }, null, 2),
);

await builder.build({
  projectDir: stage,
  publish: 'never',
  ...(win ? { win: [] } : { mac: ['dir'] }),
  config: {
    appId: 'app.desk.desktop',
    productName: 'Desk',
    copyright: 'Desk',
    directories: { app: stage, output: release, buildResources: join(root, 'build') },
    electronVersion: electronPkg.version,
    electronDist: join(dirname(require.resolve('electron/package.json')), 'dist'),
    npmRebuild: false,
    nodeGypRebuild: false,
    buildDependenciesFromSource: false,
    asar: true,
    files: ['**/*'],
    mac: {
      category: 'public.app-category.productivity',
      icon: join(root, 'build', 'icon.icns'),
      identity: null,
      target: [{ target: 'dir', arch: [arch] }],
      extendInfo: { NSHighResolutionCapable: true },
    },
    win: { icon: join(root, 'build', 'icon.png'), target: [{ target: 'nsis', arch: ['x64'] }] },
    nsis: { oneClick: false, allowToChangeInstallationDirectory: true },
  },
});
rmSync(stage, { recursive: true, force: true });
if (win) process.exit(0);

const appDir = join(release, arch === 'arm64' ? 'mac-arm64' : 'mac', 'Desk.app');
if (!existsSync(appDir)) throw new Error(`Desk.app not found at ${appDir}`);
// The bundled deskd goes in Resources/deskd with its node_modules (electron-builder's extraResources drops those),
// keeping only this platform's better-sqlite3 prebuilds, and with the first-party catalog skills (bundle.mjs).
// Electron's default app is not needed.
const resources = join(appDir, 'Contents', 'Resources');
const deskd = join(resources, 'deskd');
cpSync(join(repo, 'apps', 'daemon', 'dist'), deskd, { recursive: true });
const prebuilds = join(deskd, 'node_modules', 'better-sqlite3', 'prebuilds');
for (const f of readdirSync(prebuilds)) if (!f.startsWith('darwin-')) rmSync(join(prebuilds, f));
rmSync(join(resources, 'default_app.asar'), { force: true });
// uv sets up Python for catalog skills; the daemon finds it at deskd/bin/uv.
console.log(`bundled ${await installUv(deskd, arch)}`);
run('codesign', ['--force', '--deep', '--sign', '-', appDir]);
run('codesign', ['--verify', '--deep', '--strict', appDir]);

const name = `Desk-${pkg.version}-${arch}`;
run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', appDir, join(release, `${name}.zip`)]);
const dmgRoot = join(release, 'dmg');
mkdirSync(dmgRoot);
cpSync(appDir, join(dmgRoot, 'Desk.app'), { recursive: true, verbatimSymlinks: true });
symlinkSync('/Applications', join(dmgRoot, 'Applications'));
run('hdiutil', ['create', '-volname', 'Desk', '-srcfolder', dmgRoot, '-fs', 'HFS+', '-format', 'UDZO', '-ov', join(release, `${name}.dmg`)], { stdio: 'ignore' });
rmSync(dmgRoot, { recursive: true, force: true });
console.log(`packaged ${join(release, `${name}.dmg`)} and ${name}.zip`);
