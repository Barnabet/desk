import { mkdtempSync, readFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

type Command = { name: string; args: string[]; cwd: string; stdio?: unknown };
type WebDev = {
  webDevCommands(root: string, args?: string[]): Command[];
  runTogether(commands: Command[], o?: { log?(line: string): void }): { done: Promise<number>; stop(): void };
  stopChild(child: { pid?: number; kill(signal: string): void }, platform?: string, run?: (...args: unknown[]) => unknown): void;
};

const url = new URL('./web-dev.mjs', import.meta.url).href;
const dev = (await import(url)) as WebDev;
const { isMain } = (await import(new URL('./ng.mjs', import.meta.url).href)) as { isMain(url: string, argv1?: string): boolean };
const root = fileURLToPath(new URL('..', import.meta.url));
const node = (code: string, name = 'child'): Command => ({ name, args: ['-e', code], cwd: root, stdio: 'ignore' });

describe('pnpm web', () => {
  it('is the root web script', () => {
    const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as { scripts: Record<string, string> };
    expect(pkg.scripts['web']).toBe('node scripts/web-dev.mjs');
  });

  it('watches the Angular build in apps/web-ui and runs desk web --dev with the extra arguments', () => {
    const [build, web, ...rest] = dev.webDevCommands('/repo', ['--port', '7500', '--no-open']);
    expect(rest).toEqual([]);
    expect(build).toMatchObject({
      name: 'ng build --watch',
      args: [join('/repo', 'scripts', 'ng.mjs'), 'build', '--watch', '--configuration', 'development'],
      cwd: join('/repo', 'apps', 'web-ui'),
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    expect(web).toMatchObject({
      name: 'desk web --dev',
      args: ['--import', 'tsx', join('/repo', 'apps', 'cli', 'src', 'main.ts'), 'web', '--dev', '--port', '7500', '--no-open'],
      cwd: '/repo',
      stdio: 'inherit',
    });
  });

  it('stops the other process when one exits, and exits with its code', async () => {
    const lines: string[] = [];
    const run = dev.runTogether([node('setTimeout(() => {}, 60000)', 'server'), node('process.exit(3)', 'build')], { log: (line) => lines.push(line) });
    expect(await run.done).toBe(3);
    expect(lines).toEqual(['build stopped (exit 3); stopping the rest.']);
  });

  it('stops both on Ctrl-C and exits cleanly', async () => {
    const lines: string[] = [];
    const run = dev.runTogether([node('setTimeout(() => {}, 60000)'), node('setTimeout(() => {}, 60000)')], { log: (line) => lines.push(line) });
    run.stop();
    expect(await run.done).toBe(0);
    expect(lines).toEqual([]);
  });

  it('stops a child with SIGTERM, and on Windows its whole tree with taskkill', () => {
    const calls: unknown[][] = [];
    const run = (...args: unknown[]) => calls.push(args);
    const child = { pid: 4242, kill: vi.fn() };
    dev.stopChild(child, 'win32', run);
    expect(calls).toEqual([['taskkill', ['/pid', '4242', '/T', '/F'], { stdio: 'ignore', windowsHide: true }]]);
    expect(child.kill).not.toHaveBeenCalled();
    dev.stopChild(child, 'darwin', run);
    dev.stopChild(child, 'linux', run);
    // A child that never started has no tree to end.
    dev.stopChild({ kill: child.kill }, 'win32', run);
    expect(child.kill.mock.calls).toEqual([['SIGTERM'], ['SIGTERM'], ['SIGTERM']]);
    expect(calls).toHaveLength(1);
  });

  it('starts only as the script Node runs, also through a symlinked folder', () => {
    expect(readFileSync(fileURLToPath(url), 'utf8')).toContain('\nif (isMain(import.meta.url)) main(process.argv.slice(2));\n');
    const dir = mkdtempSync(join(tmpdir(), 'desk-web-dev-'));
    try {
      symlinkSync(join(root, 'scripts'), join(dir, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
      expect(isMain(url, join(dir, 'linked', 'web-dev.mjs'))).toBe(true);
      expect(isMain(url, join(dir, 'linked', 'ng.mjs'))).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
