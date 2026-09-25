import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';

type NgScript = {
  NODE_RANGE: string;
  supported(version: string): boolean;
  defaultNvmDir(env?: Record<string, string | undefined>, platform?: string): string | null;
  pickNode(o: { execPath: string; version: string; nvmDir: string | null; platform?: string }): string | null;
  binOf(cwd: string, pkg: string, bin: string): string;
};

const script = fileURLToPath(new URL('./ng.mjs', import.meta.url));
const ng = (await import(new URL('./ng.mjs', import.meta.url).href)) as NgScript;

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

describe('scripts/ng.mjs', () => {
  it("accepts exactly Angular 22's Node range", () => {
    expect(ng.NODE_RANGE).toBe('^22.22.3 || ^24.15.0 || >=26');
    for (const v of ['v22.22.3', 'v22.23.3', 'v22.30.0', '24.15.0', 'v24.20.1', 'v26.0.0', 'v27.2.0']) expect(ng.supported(v), v).toBe(true);
    for (const v of ['v22.21.0', 'v22.22.2', 'v23.9.0', 'v24.14.9', 'v25.1.0', 'v20.19.0', 'nonsense']) expect(ng.supported(v), v).toBe(false);
  });

  it('keeps a Node that fits, else takes the newest fitting one from nvm', () => {
    const dir = nvm('v22.21.0', 'v22.23.3', 'v24.15.1', 'v25.0.0');
    expect(ng.pickNode({ execPath: '/opt/node/bin/node', version: 'v24.15.0', nvmDir: dir })).toBe('/opt/node/bin/node');
    expect(ng.pickNode({ execPath: '/opt/node/bin/node', version: 'v22.21.0', nvmDir: dir })).toBe(join(dir, 'v24.15.1', 'bin', 'node'));
    expect(ng.pickNode({ execPath: '/opt/node/bin/node', version: 'v22.21.0', nvmDir: nvm('v22.21.0', 'v23.1.0') })).toBeNull();
    expect(ng.pickNode({ execPath: '/opt/node/bin/node', version: 'v22.21.0', nvmDir: join(temp(), 'missing') })).toBeNull();
    expect(ng.pickNode({ execPath: '/opt/node/bin/node', version: 'v22.21.0', nvmDir: null })).toBeNull();
  });

  it('skips version folders without a node binary, and knows where nvm keeps them', () => {
    const dir = nvm('v22.23.3');
    mkdirSync(join(dir, 'v24.15.1'));
    expect(ng.pickNode({ execPath: 'x', version: 'v22.21.0', nvmDir: dir })).toBe(join(dir, 'v22.23.3', 'bin', 'node'));
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

  const chosen = ng.pickNode({ execPath: process.execPath, version: process.version, nvmDir: ng.defaultNvmDir() });
  it.skipIf(chosen === null)('prints the Node it would run with --which', () => {
    const r = spawnSync(process.execPath, [script, '--which'], { encoding: 'utf8' });
    expect(r.status).toBe(0);
    const [path, version] = r.stdout.trim().split(' ');
    expect(path).toBe(chosen);
    expect(ng.supported(version ?? '')).toBe(true);
  });
});
