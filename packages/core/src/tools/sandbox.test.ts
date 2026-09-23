import { existsSync } from 'node:fs';
import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { bashTool, scrubbedEnv } from './bash';
import { buildSandboxProfile, detectSandbox, shellInvocation, type SandboxSpec } from './sandbox';
import { testToolContext } from '../testing/context';
import type { ToolContext } from './types';

let available = false;
beforeAll(async () => {
  available = await detectSandbox();
});

let ws: string;
const escapee = join(homedir(), 'desk-sandbox-escape-test');
beforeEach(async () => {
  ws = await realpath(await mkdtemp(join(tmpdir(), 'desk-sbx-')));
});
afterEach(async () => {
  await rm(ws, { recursive: true, force: true });
  await rm(escapee, { force: true });
});

const ctx = (sandbox: SandboxSpec): ToolContext => testToolContext(ws, { sandbox });

describe('sandboxed bash', () => {
  it('allows writes inside the workspace', async (t) => {
    if (!available) t.skip();
    const out = await bashTool.execute({ command: 'echo hi > inside.txt', timeout_s: 10 }, ctx({ enabled: true, writable: [ws] }));
    expect(out).toMatch(/^\[exit code 0\]/);
    expect(await readFile(join(ws, 'inside.txt'), 'utf8')).toBe('hi\n');
  });

  it('blocks writes outside the workspace', async (t) => {
    if (!available) t.skip();
    const out = await bashTool.execute({ command: `echo x > ${escapee}`, timeout_s: 10 }, ctx({ enabled: true, writable: [ws] }));
    expect(out).toMatch(/^\[exit code 1\]/);
    expect(out).toContain('operation not permitted');
    expect(existsSync(escapee)).toBe(false);
  });

  it('allows temp writes', async (t) => {
    if (!available) t.skip();
    const out = await bashTool.execute({ command: 'echo y > "$TMPDIR/desk-sbx-probe" && echo ok', timeout_s: 10 }, ctx({ enabled: true, writable: [ws] }));
    expect(out).toContain('ok');
  });

  it('with no writable paths blocks writes to a non-temp directory (read-only shell)', async (t) => {
    if (!available) t.skip();
    // Temp dirs are always writable, so use a directory outside $TMPDIR, like a real project source.
    const source = await realpath(await mkdtemp(join(process.cwd(), '.sandbox-test-')));
    try {
      const out = await bashTool.execute({ command: `echo z > ${join(source, 'nope.txt')}`, timeout_s: 10 }, { ...ctx({ enabled: true, writable: [] }), workspace: source });
      expect(out).toMatch(/^\[exit code 1\]/);
      expect(existsSync(join(source, 'nope.txt'))).toBe(false);
    } finally {
      await rm(source, { recursive: true, force: true });
    }
  });
});

describe('profile and invocation', () => {
  it('escapes quotes and backslashes in paths', () => {
    expect(buildSandboxProfile(['/a"b\\c'])).toContain('(subpath "/a\\"b\\\\c")');
  });

  it('wraps with sandbox-exec only when enabled', () => {
    expect(shellInvocation('ls', { enabled: false, writable: [] })).toEqual({ command: '/bin/zsh', args: ['-f', '-c', 'ls'] });
    const s = shellInvocation('ls', { enabled: true, writable: ['/w'] });
    expect(s.command).toBe('/usr/bin/sandbox-exec');
    expect(s.args.slice(-3)).toEqual(['-f', '-c', 'ls']);
  });
});

describe('scrubbedEnv', () => {
  it('redirects tool caches under TMPDIR and drops secrets', () => {
    const env = scrubbedEnv('/w', { PATH: '/bin', TMPDIR: '/t/', CLIPROXY_API_KEY: 'k' });
    expect(env).toMatchObject({
      PATH: '/bin',
      DESK_WORKSPACE: '/w',
      XDG_CACHE_HOME: '/t/desk-cache',
      npm_config_cache: '/t/desk-cache/npm',
      npm_config_store_dir: '/t/desk-cache/pnpm-store',
      PIP_CACHE_DIR: '/t/desk-cache/pip',
      YARN_CACHE_FOLDER: '/t/desk-cache/yarn',
    });
    expect(env).not.toHaveProperty('CLIPROXY_API_KEY');
  });
});
