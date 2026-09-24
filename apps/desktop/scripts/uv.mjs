// Fetches the pinned uv release for the packaged app's Python skill runtimes, checked against the SHA-256 recorded
// here (the same as Astral's published .sha256 files), and caches it in apps/desktop/.cache. Its licences come along.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chmodSync, cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const UV_VERSION = '0.12.18';
const ARCHIVES = {
  arm64: { name: 'uv-aarch64-apple-darwin', sha256: 'cf40e0c6a202190ccd9e0406dcfdd5b2d6668a9a5c779b17948963df32aafe5b' },
  x64: { name: 'uv-x86_64-apple-darwin', sha256: '2e4108f5395397c8bc5d43bf83d3bdbb2d0e92b90d0efa607756be704905fa33' },
};
const LICENSES = {
  'LICENSE-MIT': '860e3d7a86b84e6a7012c7a635fc64df475cebc6cce34dfeb73a5982ec58176c',
  'LICENSE-APACHE': 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4',
};

const cacheRoot = fileURLToPath(new URL('../.cache', import.meta.url));
const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');

async function download(url, expected) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Downloading ${url} failed (${res.status})`);
  const buf = Buffer.from(await res.arrayBuffer());
  if (sha256(buf) !== expected) throw new Error(`${url} does not match its pinned SHA-256`);
  return buf;
}

/** Copies uv (and its licences) for `arch` into `<deskd>/bin/uv` and `<deskd>/licenses/uv/`. */
export async function installUv(deskd, arch) {
  const archive = ARCHIVES[arch];
  if (!archive) throw new Error(`No pinned uv build for ${arch}`);
  const cache = join(cacheRoot, `uv-${UV_VERSION}-${arch}`);
  const bin = join(cache, archive.name, 'uv');
  if (!existsSync(bin) || !existsSync(join(cache, 'ok'))) {
    rmSync(cache, { recursive: true, force: true });
    mkdirSync(cache, { recursive: true });
    const tgz = await download(`https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${archive.name}.tar.gz`, archive.sha256);
    writeFileSync(join(cache, 'uv.tar.gz'), tgz);
    execFileSync('tar', ['-xzf', join(cache, 'uv.tar.gz'), '-C', cache, `${archive.name}/uv`]);
    for (const [file, hash] of Object.entries(LICENSES)) writeFileSync(join(cache, file), await download(`https://raw.githubusercontent.com/astral-sh/uv/${UV_VERSION}/${file}`, hash));
    writeFileSync(join(cache, 'ok'), UV_VERSION);
  }
  mkdirSync(join(deskd, 'bin'), { recursive: true });
  cpSync(bin, join(deskd, 'bin', 'uv'));
  chmodSync(join(deskd, 'bin', 'uv'), 0o755);
  const licenses = join(deskd, 'licenses', 'uv');
  mkdirSync(licenses, { recursive: true });
  for (const file of Object.keys(LICENSES)) cpSync(join(cache, file), join(licenses, file));
  const version = execFileSync(join(deskd, 'bin', 'uv'), ['--version'], { encoding: 'utf8' }).trim();
  if (!version.startsWith(`uv ${UV_VERSION}`)) throw new Error(`Bundled uv reports "${version}"`);
  return version;
}

