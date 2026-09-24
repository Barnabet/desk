// Regenerates build/icon.png and build/icon.icns from build/icon.svg (macOS: sips and iconutil).
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const buildDir = join(root, 'build');
const png = join(buildDir, 'icon.png');
const electron = createRequire(import.meta.url)('electron');
execFileSync(electron, [join(root, 'scripts', 'render-icon.cjs'), join(buildDir, 'icon.svg'), png], { stdio: 'inherit' });

const set = join(buildDir, 'icon.iconset');
rmSync(set, { recursive: true, force: true });
mkdirSync(set);
for (const size of [16, 32, 128, 256, 512]) {
  execFileSync('sips', ['-z', String(size), String(size), png, '--out', join(set, `icon_${size}x${size}.png`)], { stdio: 'ignore' });
  execFileSync('sips', ['-z', String(size * 2), String(size * 2), png, '--out', join(set, `icon_${size}x${size}@2x.png`)], { stdio: 'ignore' });
}
execFileSync('iconutil', ['-c', 'icns', set, '-o', join(buildDir, 'icon.icns')]);
rmSync(set, { recursive: true, force: true });
console.log(`wrote ${png} and icon.icns`);
