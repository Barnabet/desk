import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { bashReadonlyTool, bashTool, scrubbedEnv } from './bash';
import { grepTool } from './fs';
import { buildSandboxProfile, detectSandbox, sandboxGuard, shellInvocation, type SandboxGuard, type SandboxSpec } from './sandbox';
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

describe('guard', () => {
  let root: string;
  let data: string;
  let work: string;
  let secret: string;
  beforeEach(async () => {
    root = await realpath(await mkdtemp(join(tmpdir(), 'desk-guard-')));
    data = join(root, 'data');
    work = join(data, 'workspaces', 't1');
    secret = join(data, 'daemon.json');
    await mkdir(work, { recursive: true });
    await writeFile(secret, '{"token":"tok-123"}');
  });
  afterEach(async () => rm(root, { recursive: true, force: true }));

  // `root` plays a project source that contains Desk's data dir, like a source at ~.
  const spec = (ports: number[] = []): SandboxSpec => ({ enabled: true, writable: [work, root], guard: sandboxGuard({ dataDir: data, secrets: [secret], ports }) });
  const run = (command: string, sandbox = spec()) => bashTool.execute({ command, timeout_s: 20 }, testToolContext(work, { sandbox }));

  it('keeps secrets unreadable: directly, copied, hard-linked, or by moving their folder', async (t) => {
    if (!available) t.skip();
    const moved = join(root, 'moved');
    const out = await run(`cat '${secret}'; cp '${secret}' copy; ln '${secret}' hard; mv '${data}' '${moved}'; cat '${join(moved, 'daemon.json')}'; mv '${secret}' '${join(root, 'x')}'; echo end`);
    expect(out).toContain('end');
    expect(out).not.toContain('tok-123');
    expect(existsSync(secret)).toBe(true);
    expect(existsSync(join(work, 'copy'))).toBe(false);
    expect(existsSync(join(work, 'hard'))).toBe(false);
  });

  it("keeps Desk's data dir read-only outside the workspace, even under a writable source", async (t) => {
    if (!available) t.skip();
    const out = await run(`echo a > mine.txt && echo b > '${join(root, 'source.txt')}' && echo wrote; echo c > '${join(data, 'config.json')}'`);
    expect(out).toContain('wrote');
    expect(await readFile(join(work, 'mine.txt'), 'utf8')).toBe('a\n');
    expect(existsSync(join(root, 'source.txt'))).toBe(true);
    expect(existsSync(join(data, 'config.json'))).toBe(false);
  });

  it("blocks connecting to deskd's port and running the Keychain CLI", async (t) => {
    if (!available) t.skip();
    const server = createServer((_req, res) => res.end('hello from deskd'));
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = (server.address() as AddressInfo).port;
    try {
      const probe = `curl -s -m 5 http://127.0.0.1:${port}/; echo " curl=$?"; curl -s -m 5 http://localhost:${port}/; /usr/bin/security list-keychains`;
      expect(await run(`curl -s -m 5 http://127.0.0.1:${port}/`)).toContain('hello from deskd');
      const out = await run(probe, spec([port]));
      expect(out).not.toContain('hello from deskd');
      expect(out).toContain('curl=7');
      expect(out).toMatch(/operation not permitted: \/usr\/bin\/security/);
    } finally {
      server.close();
    }
  });

  it("keeps the git config, hooks and links of the repos deskd runs git in read-only, and nothing else's", async (t) => {
    if (!available) t.skip();
    const repo = join(root, 'repo');
    const git = (cwd: string, ...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8' });
    await mkdir(repo);
    git(repo, 'init', '-q');
    git(repo, '-c', 'user.name=T', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'init');
    const wt = join(data, 'workspaces', 't2');
    git(repo, 'worktree', 'add', '-q', '-b', 't2', wt);
    const common = join(repo, '.git');
    const sandbox: SandboxSpec = { enabled: true, writable: [wt, common], guard: sandboxGuard({ dataDir: data, secrets: [secret] }), gitDirs: [common, join(wt, '.git')] };
    const out = await bashTool.execute(
      {
        command: [
          `echo '#!/bin/sh' > '${join(common, 'hooks', 'pre-commit')}' || echo hook-denied`,
          `git config --local core.fsmonitor 'echo pwn' || echo config-denied`,
          `echo 'gitdir: /tmp/evil' > .git || echo link-denied`,
          `echo x > '${join(common, 'worktrees', 't2', 'commondir')}' || echo commondir-denied`,
          `echo hi > f.txt && git add f.txt && git -c user.name=T -c user.email=t@t commit -q -m work && echo committed`,
          `git clone -q '${repo}' sub && git -C sub config --local x.y 1 && echo cloned`,
        ].join('\n'),
        timeout_s: 30,
      },
      testToolContext(wt, { sandbox }),
    );
    for (const word of ['hook-denied', 'config-denied', 'link-denied', 'commondir-denied', 'committed', 'cloned']) expect(out).toContain(word);
    // Unset: `git config --get` exits 1.
    expect(() => git(repo, 'config', '--get', 'core.fsmonitor')).toThrow();
  });

  it('keeps the git and ssh config read-only even under a writable source around them', async (t) => {
    if (!available) t.skip();
    const home = join(root, 'home');
    await mkdir(join(home, '.ssh'), { recursive: true });
    const sandbox: SandboxSpec = { enabled: true, writable: [work, home], guard: sandboxGuard({ dataDir: data, secrets: [secret], readOnly: [join(home, '.gitconfig'), join(home, '.ssh')] }) };
    const out = await bashTool.execute(
      { command: `cd '${home}'; echo '[filter "x"]' > .gitconfig || echo gitconfig-denied; echo 'Host *' > .ssh/config || echo ssh-denied; echo ok > notes.txt && echo wrote`, timeout_s: 20 },
      testToolContext(work, { sandbox }),
    );
    for (const word of ['gitconfig-denied', 'ssh-denied', 'wrote']) expect(out).toContain(word);
    expect(existsSync(join(home, '.gitconfig'))).toBe(false);
  });

  it('runs the grep tool sandboxed', async (t) => {
    if (!available) t.skip();
    await writeFile(join(work, 'notes.md'), 'find me\n');
    const ctx = testToolContext(work, { sandbox: spec() });
    expect(await grepTool.execute({ pattern: 'find me', root: '.' }, ctx)).toBe('notes.md:1:find me');
  });

  it('applies to read-only shells too', async (t) => {
    if (!available) t.skip();
    const out = await bashReadonlyTool.execute({ command: `cat '${secret}'; echo end`, timeout_s: 20 }, testToolContext(work, { sandbox: spec() }));
    expect(out).toContain('end');
    expect(out).not.toContain('tok-123');
  });
});

describe('profile and invocation', () => {
  it('puts the guard after the writable roots: data dir, secrets and the folders above them, the Keychain CLI, ports', () => {
    const g: SandboxGuard = { dataDir: '/d', secrets: ['/d/daemon.json', '/h/.config/cliproxyapi.env'], readOnly: ['/h/.gitconfig', '/h/.ssh'], ports: [7433] };
    const p = buildSandboxProfile(['/d/workspaces/t', '/h'], g);
    expect(p.indexOf('(deny file-write*\n  (subpath "/d")\n)')).toBeGreaterThan(p.indexOf('  (subpath "/h")'));
    // Only roots inside the data dir are allowed again; a root around it (like a home folder) is not.
    expect(p).toContain('(allow file-write*\n  (subpath "/d/workspaces/t")\n)');
    expect(p).toContain('(deny file-write*\n  (literal "/d/daemon.json")\n  (literal "/d")\n  (literal "/h/.config/cliproxyapi.env")\n  (literal "/h/.config")\n  (literal "/h")\n)');
    expect(p).toContain('(deny file-read*\n  (literal "/d/daemon.json")\n  (literal "/h/.config/cliproxyapi.env")\n)');
    expect(p).toContain('(deny file-write*\n  (subpath "/h/.gitconfig")\n  (subpath "/h/.ssh")\n)');
    expect(p).toContain('(deny process-exec (literal "/usr/bin/security"))');
    expect(p.trimEnd().endsWith('(deny network-outbound (remote ip "localhost:7433"))')).toBe(true);
    expect(buildSandboxProfile(['/w'])).not.toContain('deny file-read');
    const withGit = buildSandboxProfile(['/w'], g, ['/r/.git']);
    expect(withGit).toContain('(deny file-write*\n  (literal "/r/.git")\n  (subpath "/r/.git/hooks")\n  (literal "/r/.git/config")\n  (regex #"^/r/\\.git/(worktrees/');
  });

  it('carries the guard into the profile, including for a read-only shell', () => {
    const guard = sandboxGuard({ dataDir: '/nonexistent-desk-data', secrets: ['/nonexistent-desk-data/daemon.json'], ports: [7433] });
    const profile = shellInvocation('ls', { enabled: true, writable: [], guard }).args[1]!;
    expect(profile).toContain('(literal "/nonexistent-desk-data/daemon.json")');
    expect(profile).toContain('localhost:7433');
  });

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
