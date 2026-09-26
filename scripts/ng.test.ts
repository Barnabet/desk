import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';

type NgScript = {
  NODE_RANGE: string;
  supported(version: string): boolean;
  defaultNvmDir(env?: Record<string, string | undefined>, platform?: string): string | null;
  pickNode(o: { execPath: string; version: string; nvmDir: string | null; platform?: string }): string | null;
  binOf(cwd: string, pkg: string, bin: string): string;
  isMain(url: string, argv1?: string): boolean;
};

const url = new URL('./ng.mjs', import.meta.url).href;
const script = fileURLToPath(url);
const ng = (await import(url)) as NgScript;

const dirs: string[] = [];
const temp = (): string => {
  const dir = mkdtempSync(join(tmpdir(), 'desk-ng-'));
  dirs.push(dir);
  return dir;
};
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

/** A fake ~/.nvm/versions/node with a node binary for each version. */
function nvm(...versions: string[]): string {
  const dir = temp();
  for (const v of versions) {
    mkdirSync(join(dir, v, 'bin'), { recursive: true });
    writeFileSync(join(dir, v, 'bin', 'node'), '');
  }
  return dir;
}

/** scripts/ reached through a symlink (a junction on Windows, which needs no privilege). */
function linkedScripts(): string {
  const link = join(temp(), 'linked');
  symlinkSync(dirname(script), link, process.platform === 'win32' ? 'junction' : 'dir');
  return link;
}

/** A package under `cwd` whose `bin` prints how it was run (Node, first PATH entry, arguments) and exits with `code`. */
function fakeBin(cwd: string, pkg: string, bin: string, code: number): void {
  const dir = join(cwd, 'node_modules', ...pkg.split('/'));
  mkdirSync(join(dir, 'bin'), { recursive: true });
  writeFileSync(join(dir, 'package.json'), JSON.stringify({ name: pkg, bin: { [bin]: `./bin/${bin}.js` } }));
  writeFileSync(
    join(dir, 'bin', `${bin}.js`),
    `process.stdout.write(JSON.stringify({ node: process.execPath, path: (process.env.PATH || '').split(${JSON.stringify(delimiter)})[0], args: process.argv.slice(2), analytics: process.env.NG_CLI_ANALYTICS })); process.exitCode = ${code};`,
  );
}

describe('scripts/ng.mjs', () => {
  it("accepts exactly Angular 22's Node range", () => {
    expect(ng.NODE_RANGE).toBe('^22.22.3 || ^24.15.0 || >=26');
    for (const v of ['v22.22.3', 'v22.23.3', 'v22.30.0', '24.15.0', 'v24.20.1', 'v26.0.0', 'v27.2.0']) expect(ng.supported(v), v).toBe(true);
    for (const v of ['v22.21.0', 'v22.22.2', 'v23.9.0', 'v24.14.9', 'v25.1.0', 'v20.19.0', 'nonsense']) expect(ng.supported(v), v).toBe(false);
  });

  it('keeps a Node that fits, else takes the newest fitting one from nvm', () => {
    const dir = nvm('v22.21.0', 'v22.23.3', 'v24.15.1', 'v25.0.0');
    const from = (version: string, nvmDir: string | null) => ng.pickNode({ execPath: '/opt/node/bin/node', version, nvmDir, platform: 'linux' });
    expect(from('v24.15.0', dir)).toBe('/opt/node/bin/node');
    expect(from('v22.21.0', dir)).toBe(join(dir, 'v24.15.1', 'bin', 'node'));
    expect(from('v22.21.0', nvm('v22.21.0', 'v23.1.0'))).toBeNull();
    expect(from('v22.21.0', join(temp(), 'missing'))).toBeNull();
    expect(from('v22.21.0', null)).toBeNull();
  });

  it("finds nvm-windows' node.exe under NVM_HOME on Windows", () => {
    const dir = temp();
    for (const v of ['v22.21.0', 'v24.15.1']) {
      mkdirSync(join(dir, v));
      writeFileSync(join(dir, v, 'node.exe'), '');
    }
    mkdirSync(join(dir, 'v22.23.3', 'bin'), { recursive: true });
    writeFileSync(join(dir, 'v22.23.3', 'bin', 'node'), '');
    expect(ng.pickNode({ execPath: 'C:\\node\\node.exe', version: 'v22.21.0', nvmDir: dir, platform: 'win32' })).toBe(join(dir, 'v24.15.1', 'node.exe'));
    expect(ng.pickNode({ execPath: 'x', version: 'v22.21.0', nvmDir: dir, platform: 'linux' })).toBe(join(dir, 'v22.23.3', 'bin', 'node'));
  });

  it('skips version folders without a node binary, and knows where nvm keeps them', () => {
    const dir = nvm('v22.23.3');
    mkdirSync(join(dir, 'v24.15.1'));
    expect(ng.pickNode({ execPath: 'x', version: 'v22.21.0', nvmDir: dir, platform: 'linux' })).toBe(join(dir, 'v22.23.3', 'bin', 'node'));
    expect(ng.defaultNvmDir({ NVM_DIR: '/home/me/.nvm' }, 'linux')).toBe(join('/home/me/.nvm', 'versions', 'node'));
    expect(ng.defaultNvmDir({ NVM_HOME: 'C:\\nvm' }, 'win32')).toBe('C:\\nvm');
    expect(ng.defaultNvmDir({}, 'win32')).toBeNull();
  });

  it("finds a package's bin script", () => {
    const cwd = temp();
    mkdirSync(join(cwd, 'node_modules', '@angular', 'cli'), { recursive: true });
    writeFileSync(join(cwd, 'node_modules', '@angular', 'cli', 'package.json'), JSON.stringify({ name: '@angular/cli', bin: { ng: './bin/ng.js' } }));
    expect(ng.binOf(cwd, '@angular/cli', 'ng')).toBe(join(cwd, 'node_modules', '@angular', 'cli', 'bin', 'ng.js'));
    expect(() => ng.binOf(cwd, '@angular/cli', 'ngc')).toThrow('@angular/cli has no "ngc" bin');
  });

  it('knows it is the script Node started, also through a symlinked folder', () => {
    const link = linkedScripts();
    expect(ng.isMain(url, script)).toBe(true);
    expect(ng.isMain(url, join(link, 'ng.mjs'))).toBe(true);
    expect(ng.isMain(url, join(link, 'web-dev.mjs'))).toBe(false);
    expect(ng.isMain(url, join(link, 'missing.mjs'))).toBe(false);
    expect(ng.isMain(url, undefined)).toBe(false);
  });

  const chosen = ng.pickNode({ execPath: process.execPath, version: process.version, nvmDir: ng.defaultNvmDir() });
  it.skipIf(chosen === null)('prints the Node it would run with --which', () => {
    const r = spawnSync(process.execPath, [script, '--which'], { encoding: 'utf8' });
    expect(r.status).toBe(0);
    const [path, version] = r.stdout.trim().split(' ');
    expect(path).toBe(chosen);
    expect(ng.supported(version ?? '')).toBe(true);
    // Called through a symlinked folder, it still runs (and does not exit 0 having done nothing).
    const linked = spawnSync(process.execPath, [join(linkedScripts(), 'ng.mjs'), '--which'], { encoding: 'utf8' });
    expect(linked.status).toBe(0);
    expect(linked.stdout).toBe(r.stdout);
  });

  it.skipIf(chosen === null)('runs the CLI or ngc from the cwd under the chosen Node, first on PATH, and passes its exit code on', () => {
    const cwd = temp();
    fakeBin(cwd, '@angular/cli', 'ng', 7);
    fakeBin(cwd, '@angular/compiler-cli', 'ngc', 0);
    const run = (...args: string[]) => {
      const r = spawnSync(process.execPath, [script, ...args], { cwd, encoding: 'utf8' });
      if (!r.stdout) throw new Error(r.stderr || `ng.mjs printed nothing (exit ${r.status})`);
      return { status: r.status, ...(JSON.parse(r.stdout) as { node: string; path: string; args: string[]; analytics: string }) };
    };
    const cli = run('build', '--x');
    expect(cli).toMatchObject({ status: 7, path: dirname(chosen!), args: ['build', '--x'], analytics: 'false' });
    expect(realpathSync(cli.node)).toBe(realpathSync(chosen!));
    const ngc = run('--ngc', '-p', 't.json');
    expect(ngc).toMatchObject({ status: 0, path: dirname(chosen!), args: ['-p', 't.json'], analytics: 'false' });
    expect(realpathSync(ngc.node)).toBe(realpathSync(chosen!));
  });
});
