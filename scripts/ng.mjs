#!/usr/bin/env node
/**
 * Runs the Angular CLI for apps/web-ui under a Node that Angular 22 accepts (^22.22.3 || ^24.15.0 || >=26).
 *   node ../../scripts/ng.mjs <ng args>      from apps/web-ui: `build`, `test --watch=false`, …
 *   node ../../scripts/ng.mjs --ngc <args>   the Angular compiler instead (`--ngc -p tsconfig.app.json --noEmit`)
 *   node scripts/ng.mjs --which              prints the Node it would use and its version
 * It keeps the current Node when it fits, else takes the newest fitting one from nvm (shells the Claude app starts
 * inherit an older Node on PATH), else exits with a message. The rest of the repository keeps its own Node.
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, realpathSync } from 'node:fs';
import { homedir } from 'node:os';
import { delimiter, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const NODE_RANGE = '^22.22.3 || ^24.15.0 || >=26';

const parse = (version) => {
  const m = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(String(version).trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
};

/** Whether a Node version ("v22.23.3" or "22.23.3") is in NODE_RANGE. */
export function supported(version) {
  const v = parse(version);
  if (!v) return false;
  const [major, minor, patch] = v;
  if (major === 22) return minor > 22 || (minor === 22 && patch >= 3);
  if (major === 24) return minor >= 15;
  return major >= 26;
}

const newestFirst = (a, b) => {
  const x = parse(a) ?? [0, 0, 0];
  const y = parse(b) ?? [0, 0, 0];
  return y[0] - x[0] || y[1] - x[1] || y[2] - x[2];
};

/** Where nvm keeps its Node versions (nvm-windows: NVM_HOME), or null. */
export function defaultNvmDir(env = process.env, platform = process.platform) {
  if (platform === 'win32') return env.NVM_HOME ?? null;
  return join(env.NVM_DIR ?? join(homedir(), '.nvm'), 'versions', 'node');
}

/** The Node binary to run Angular with: this one if it fits, else the newest fitting one under `nvmDir`, else null. */
export function pickNode({ execPath, version, nvmDir, platform = process.platform }) {
  if (supported(version)) return execPath;
  if (!nvmDir || !existsSync(nvmDir)) return null;
  const binary = (v) => (platform === 'win32' ? join(nvmDir, v, 'node.exe') : join(nvmDir, v, 'bin', 'node'));
  const fits = readdirSync(nvmDir)
    .filter((v) => supported(v) && existsSync(binary(v)))
    .sort(newestFirst);
  return fits[0] ? binary(fits[0]) : null;
}

/** The script behind a package's bin, read from its package.json under `cwd`, so it runs under the chosen Node. */
export function binOf(cwd, pkg, bin) {
  const dir = join(cwd, 'node_modules', ...pkg.split('/'));
  const manifest = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'));
  const rel = typeof manifest.bin === 'string' ? manifest.bin : manifest.bin?.[bin];
  if (!rel) throw new Error(`${pkg} has no "${bin}" bin`);
  return join(dir, rel);
}

/**
 * Whether the module at `url` (its import.meta.url) is the script Node was started with. Both sides are real paths: Node
 * resolves symlinks in import.meta.url but not in argv[1], so a script run through a symlinked folder would otherwise
 * exit 0 without doing anything.
 */
export function isMain(url, argv1 = process.argv[1]) {
  if (!argv1) return false;
  try {
    return realpathSync(argv1) === realpathSync(fileURLToPath(url));
  } catch {
    return false;
  }
}

function main(args) {
  const node = pickNode({ execPath: process.execPath, version: process.version, nvmDir: defaultNvmDir() });
  if (!node) {
    console.error(`Angular 22 needs Node ${NODE_RANGE}. This is ${process.version}, and nvm has none that fits. Install one (nvm install 22) and run this again.`);
    process.exit(1);
  }
  if (args[0] === '--which') {
    const v = spawnSync(node, ['--version'], { encoding: 'utf8' });
    console.log(`${node} ${v.stdout.trim()}`);
    return;
  }
  const ngc = args[0] === '--ngc';
  const entry = ngc ? binOf(process.cwd(), '@angular/compiler-cli', 'ngc') : binOf(process.cwd(), '@angular/cli', 'ng');
  const env = { ...process.env, PATH: `${dirname(node)}${delimiter}${process.env.PATH ?? ''}`, NG_CLI_ANALYTICS: 'false' };
  const child = spawn(node, [entry, ...(ngc ? args.slice(1) : args)], { stdio: 'inherit', env });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
}

if (isMain(import.meta.url)) main(process.argv.slice(2));
