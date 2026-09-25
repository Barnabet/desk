import { createHash } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { basename, dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { CatalogEntry } from '@desk/protocol';
import { getDeskAgent } from '../state/queries';
import { executeToolCall } from '../tools/registry';
import { skillReadTool, skillRunTool } from '../tools/skills';
import { withSkillEnv, scrubbedEnv } from '../tools/bash';
import type { Runtime } from '../runtime/runtime';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { testToolContext } from '../testing/context';
import { makeTarball } from '../testing/tarball';
import { findBrowser, findBrowsers, runtimeNote, SkillRuntimes, type Exec, type InstalledBrowser, type SkillEnv } from './runtimes';

let h: Harness;
let server: Server | undefined;
let exists: boolean;
beforeEach(async () => {
  h = await createHarness();
  exists = true;
});
afterEach(async () => {
  await new Promise<void>((r) => (server ? server.close(() => r()) : r()));
  server = undefined;
  delete process.env.DESK_OPENAI_API_KEY;
  await h.cleanup();
});

/** A uv stand-in: logs its arguments and the variables it sees, creates venvs, and fails `pip` when asked to. */
function uvStub(failPip = false): { uv: string; log: string } {
  const log = join(h.dir, 'uv.log');
  const uv = join(h.dir, failPip ? 'uv-fail' : 'uv');
  writeFileSync(
    uv,
    [
      '#!/bin/sh',
      `echo "$* | pref=$UV_PYTHON_PREFERENCE key=\${DESK_OPENAI_API_KEY:-none}" >> '${log}'`,
      'if [ "$1" = venv ]; then eval last=\\${$#}; mkdir -p "$last/bin"; printf \'#!/bin/sh\\necho py-ok\\n\' > "$last/bin/python"; chmod +x "$last/bin/python"; fi',
      failPip ? 'if [ "$1" = pip ]; then echo "error: no wheel for pandas==9.9.9 on this platform" >&2; exit 2; fi' : '',
      'exit 0',
      '',
    ].join('\n'),
  );
  chmodSync(uv, 0o755);
  return { uv, log };
}

const entry = (runtime: CatalogEntry['runtime']): CatalogEntry => ({
  id: 'tool',
  title: 'Tool',
  category: 'code',
  summary: 's',
  license: 'MIT',
  homepage: 'https://example.com',
  source: { type: 'builtin', path: 'tool' },
  digest: `sha256:${'0'.repeat(64)}`,
  files: 1,
  bytes: 1,
  scripts: 0,
  runtime,
  caveats: [],
});

const make = (o: Partial<ConstructorParameters<typeof SkillRuntimes>[0]> = {}) =>
  new SkillRuntimes({ dataDir: h.dir, store: h.store, uv: null, nodeExec: process.execPath, exists: () => exists, ...o });
const ref = { scope: 'global' as const, name: 'tool' };

/** A tiny npm registry serving tarballs built on the fly. */
async function registry(pkgs: Record<string, Buffer>): Promise<string> {
  server = createServer((req, res) => {
    const body = pkgs[req.url ?? ''];
    if (!body) return void res.writeHead(404).end();
    res.writeHead(200, { 'content-type': 'application/octet-stream' }).end(body);
  });
  await new Promise<void>((r) => server!.listen(0, '127.0.0.1', r));
  return `http://127.0.0.1:${(server!.address() as AddressInfo).port}`;
}
const sri = (b: Buffer) => `sha512-${createHash('sha512').update(b).digest('base64')}`;

describe('SkillRuntimes', () => {
  it('builds a Python environment with uv: managed Python, prebuilt wheels, exact pins, no inherited secrets', async () => {
    process.env.DESK_OPENAI_API_KEY = 'sk-should-not-leak';
    const { uv, log } = uvStub();
    const r = make({ uv });
    r.setup(ref, entry({ python: { version: '3.12', packages: ['requests==2.32.5'] } }), '2026-09-24');
    expect(r.state(ref)).toEqual({ state: 'preparing', reason: null });
    await r.settled(ref);
    expect(r.state(ref)).toEqual({ state: 'ready', reason: null });
    const dir = r.dir(ref);
    expect(readFileSync(log, 'utf8').trim().split('\n')).toEqual([
      `venv --no-config --python 3.12 ${dir}/py | pref=only-managed key=none`,
      `pip install --no-config --python ${dir}/py/bin/python --only-binary :all: --compile-bytecode --exclude-newer 2026-09-24T23:59:59Z requests==2.32.5 | pref=only-managed key=none`,
    ]);
    const env = r.env(ref);
    expect(env.bins).toEqual([join(dir, 'bin'), join(dir, 'py', 'bin')]);
    expect(env.vars).toMatchObject({ VIRTUAL_ENV: join(dir, 'py'), PYTHONDONTWRITEBYTECODE: '1', PYTHONUTF8: '1' });
    expect(h.store.list({ types: ['skill.runtime_changed'] }).map((e) => e.type === 'skill.runtime_changed' && e.payload.state)).toEqual(['preparing', 'ready']);
    expect(env.note).toMatch(/^Desk set up this skill's runtime: Python 3\.12 with requests==2\.32\.5 \(run scripts with python3\)\. Skip any install steps/);
  });

  it('answers uv run and pip install from the environment instead of installing anything', async () => {
    const r = make({ uv: uvStub().uv });
    r.setup(ref, entry({ python: { version: '3.12', packages: ['openpyxl==3.1.5'] } }), '2026-09-24');
    await r.settled(ref);
    const env = withSkillEnv({ PATH: '/usr/bin:/bin' }, r.env(ref)) as NodeJS.ProcessEnv;
    const sh = (cmd: string) => spawnSync('/bin/sh', ['-c', cmd], { env, encoding: 'utf8' });
    expect(sh('uv run --with openpyxl --python 3.12 scripts/report.py --out x').stdout.trim()).toBe('py-ok');
    expect(sh('uv run python -c 1').stdout.trim()).toBe('py-ok');
    const pip = sh('pip install openpyxl && uv pip install openpyxl');
    expect(pip.status).toBe(0);
    expect(pip.stderr).toContain('already installed this skill\'s Python packages (openpyxl==3.1.5)');
    expect(sh('uv add requests').status).toBe(1);
  });

  it('records a failure with the installer error, and with no uv at all', async () => {
    const r = make({ uv: uvStub(true).uv });
    r.setup(ref, entry({ python: { version: '3.12', packages: ['pandas==9.9.9'] } }), '2026-09-24');
    await r.settled(ref);
    expect(r.state(ref).state).toBe('failed');
    expect(r.state(ref).reason).toMatch(/uv-fail pip failed: error: no wheel for pandas==9\.9\.9/);
    expect(r.env(ref)).toMatchObject({ bins: [], vars: {} });

    const none = make({ uv: null });
    none.setup(ref, entry({ python: { version: '3.12', packages: [] } }), '2026-09-24');
    await none.settled(ref);
    expect(none.state(ref).reason).toMatch(/uv is not available/);
  });

  it('installs Node packages from the lock with integrity checks and no scripts, and resolves them from skill scripts', async () => {
    const pkg = makeTarball('package', [
      { name: 'package.json', content: JSON.stringify({ name: 'hello-pkg', version: '1.0.0', type: 'module', exports: './index.mjs', bin: { hello: 'cli.mjs' }, scripts: { postinstall: 'touch /tmp/desk-should-not-run' } }) },
      { name: 'index.mjs', content: "export const hi = 'hi from pkg';\n" },
      { name: 'cli.mjs', content: "import { hi } from './index.mjs';\nconsole.log(`cli: ${hi}`);\n" },
    ]);
    const base = await registry({ '/hello-pkg/-/hello-pkg-1.0.0.tgz': pkg });
    const r = make({ registry: base });
    const lock = [
      { name: 'hello-pkg', version: '1.0.0', integrity: sri(pkg), path: 'node_modules/hello-pkg' },
      { name: '@x/win-only', version: '1.0.0', integrity: sri(pkg), path: 'node_modules/@x/win-only', os: ['win32'] },
    ];
    r.setup(ref, entry({ node: { lock } }), '2026-09-24');
    await r.settled(ref);
    expect(r.state(ref)).toEqual({ state: 'ready', reason: null });
    const dir = r.dir(ref);
    expect(existsSync(join(dir, 'node', 'node_modules', '@x'))).toBe(false);

    // A skill script elsewhere imports the package by name.
    const skillDir = join(h.dir, 'skills', 'tool', 'scripts');
    mkdirSync(skillDir, { recursive: true });
    writeFileSync(join(skillDir, 'run.mjs'), "import { hi } from 'hello-pkg';\nconsole.log(hi);\n");
    // Scripts that spawn Node again (process.execPath) keep resolving from the environment.
    writeFileSync(join(skillDir, 'spawn.mjs'), "import { spawnSync } from 'node:child_process';\nconst r = spawnSync(process.execPath, [new URL('./run.mjs', import.meta.url).pathname], { encoding: 'utf8' });\nprocess.stdout.write(`child: ${r.stdout}${r.stderr}`);\n");
    const env = withSkillEnv({ PATH: '/usr/bin:/bin' }, r.env(ref));
    const run = spawnSync('node', [join(skillDir, 'run.mjs')], { env: env as NodeJS.ProcessEnv, encoding: 'utf8' });
    expect(run.stderr).toBe('');
    expect(run.stdout.trim()).toBe('hi from pkg');
    const nested = spawnSync('node', [join(skillDir, 'spawn.mjs')], { env: env as NodeJS.ProcessEnv, encoding: 'utf8' });
    expect(nested.stdout.trim()).toBe('child: hi from pkg');
    const cli = spawnSync('hello', [], { env: env as NodeJS.ProcessEnv, encoding: 'utf8' });
    expect(cli.stdout.trim()).toBe('cli: hi from pkg');
    const sh = (cmd: string) => spawnSync('/bin/sh', ['-c', cmd], { env: env as NodeJS.ProcessEnv, encoding: 'utf8' });
    expect(sh('npm install -g hello-pkg').status).toBe(0);
    expect(sh('npx -y hello@1.0.0').stdout.trim()).toBe('cli: hi from pkg');
    expect(sh('npx cowsay').stderr).toContain("npx: cowsay is not part of this skill's runtime");
    expect(sh('npm publish').status).toBe(1);
    expect(r.env(ref).note).toContain('Node packages hello-pkg@1.0.0');
  });

  it('fails a Node package whose tarball does not match its integrity', async () => {
    const pkg = makeTarball('package', [{ name: 'package.json', content: '{"name":"evil"}' }]);
    const base = await registry({ '/evil/-/evil-1.0.0.tgz': pkg });
    const r = make({ registry: base });
    r.setup(ref, entry({ node: { lock: [{ name: 'evil', version: '1.0.0', integrity: `sha512-${Buffer.alloc(64).toString('base64')}`, path: 'node_modules/evil' }] } }), '2026-09-24');
    await r.settled(ref);
    expect(r.state(ref).reason).toMatch(/evil@1\.0\.0 does not match its integrity hash/);
  });

  it('keeps no runtime for skills that need none, removes environments, and cleans up orphans', async () => {
    const r = make({ uv: uvStub().uv });
    r.setup(ref, entry({}), '2026-09-24');
    expect(r.state(ref)).toEqual({ state: 'none', reason: null });
    expect(h.store.list({ types: ['skill.runtime_changed'] })).toEqual([]);

    r.setup(ref, entry({ python: { version: '3.12', packages: [] } }), '2026-09-24');
    await r.settled(ref);
    expect(r.report().envs).toEqual([{ scope: 'global', project_id: null, name: 'tool', bytes: expect.any(Number), orphan: false }]);
    exists = false;
    expect(r.report().envs[0]!.orphan).toBe(true);
    expect(r.cleanup()).toMatchObject({ removed: 1 });
    expect(existsSync(r.dir(ref))).toBe(false);
    expect(r.state(ref)).toEqual({ state: 'none', reason: null });
  });
});

describe('findBrowser', () => {
  const only =
    (...paths: string[]) =>
    (p: string) =>
      paths.includes(p);

  it('prefers Chrome, then Edge, then Chromium, in /Applications or ~/Applications on macOS', () => {
    const mac = (...paths: string[]) => findBrowser({ platform: 'darwin', env: {}, home: '/Users/u', isExecutable: only(...paths) });
    const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
    const userChrome = '/Users/u/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
    const userEdge = '/Users/u/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
    const chromium = '/Applications/Chromium.app/Contents/MacOS/Chromium';
    expect(mac(chromium, userEdge, chrome)).toEqual({ name: 'Google Chrome', path: chrome });
    expect(mac(userChrome, userEdge)).toEqual({ name: 'Google Chrome', path: userChrome });
    expect(mac(chromium, userEdge)).toEqual({ name: 'Microsoft Edge', path: userEdge });
    expect(mac(chromium)).toEqual({ name: 'Chromium', path: chromium });
    expect(mac('/Applications/Firefox.app/Contents/MacOS/firefox')).toBeNull();
  });

  it('looks for Chrome, then Edge, under Program Files, Program Files (x86) and LOCALAPPDATA on Windows', () => {
    const env = { ProgramFiles: 'C:\\Program Files', 'PROGRAMFILES(X86)': 'C:\\Program Files (x86)', LocalAppData: 'C:\\Users\\u\\AppData\\Local' };
    const win = (...paths: string[]) => findBrowser({ platform: 'win32', env, home: 'C:\\Users\\u', isExecutable: only(...paths) });
    const edge = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
    const userChrome = 'C:\\Users\\u\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe';
    const userEdge = 'C:\\Users\\u\\AppData\\Local\\Microsoft\\Edge\\Application\\msedge.exe';
    expect(win(edge, userChrome)).toEqual({ name: 'Google Chrome', path: userChrome });
    expect(win(userEdge, edge)).toEqual({ name: 'Microsoft Edge', path: edge });
    expect(win(userEdge)).toEqual({ name: 'Microsoft Edge', path: userEdge });
    expect(win('C:\\Program Files\\Mozilla Firefox\\firefox.exe')).toBeNull();
    // Unset variables are skipped rather than resolved against the working directory.
    expect(findBrowser({ platform: 'win32', env: {}, home: 'C:\\Users\\u', isExecutable: () => true })).toBeNull();
  });

  it('finds google-chrome, chromium or microsoft-edge on PATH on Linux', () => {
    const linux = (...paths: string[]) => findBrowser({ platform: 'linux', env: { PATH: '/usr/local/bin::/usr/bin' }, home: '/home/u', isExecutable: only(...paths) });
    expect(linux('/usr/bin/microsoft-edge', '/usr/bin/chromium', '/usr/bin/google-chrome')).toEqual({ name: 'Google Chrome', path: '/usr/bin/google-chrome' });
    expect(linux('/usr/bin/microsoft-edge', '/usr/local/bin/chromium')).toEqual({ name: 'Chromium', path: '/usr/local/bin/chromium' });
    expect(linux('/usr/bin/microsoft-edge')).toEqual({ name: 'Microsoft Edge', path: '/usr/bin/microsoft-edge' });
    expect(linux('/opt/google/chrome/chrome')).toBeNull();
    expect(findBrowser({ platform: 'linux', env: {}, home: '/home/u', isExecutable: () => true })).toBeNull();
  });

  it('ignores relative PATH entries, which would resolve against another directory when the browser starts', () => {
    const probed: string[] = [];
    const found = findBrowser({ platform: 'linux', env: { PATH: '.:bin:/usr/bin' }, home: '/home/u', isExecutable: (p) => (probed.push(p), p.endsWith('chromium')) });
    expect(found).toEqual({ name: 'Chromium', path: '/usr/bin/chromium' });
    expect(probed).toEqual(['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/microsoft-edge']);
  });

  it('lists every installed browser, best first, for the build to try in turn', () => {
    const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
    const edge = '/Users/u/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
    expect(findBrowsers({ platform: 'darwin', env: {}, home: '/Users/u', isExecutable: only(edge, chrome) })).toEqual([
      { name: 'Google Chrome', path: chrome },
      { name: 'Microsoft Edge', path: edge },
    ]);
    expect(findBrowsers({ platform: 'darwin', env: {}, home: '/Users/u', isExecutable: () => false })).toEqual([]);
  });

  it('counts only executable files by default', () => {
    const bin = join(h.dir, 'path-bin');
    mkdirSync(join(bin, 'google-chrome'), { recursive: true });
    writeFileSync(join(bin, 'chromium'), '#!/bin/sh\n');
    writeFileSync(join(bin, 'microsoft-edge'), '#!/bin/sh\n');
    chmodSync(join(bin, 'microsoft-edge'), 0o755);
    expect(findBrowser({ platform: 'linux', env: { PATH: bin }, home: h.dir })).toEqual({ name: 'Microsoft Edge', path: join(bin, 'microsoft-edge') });
  });
});

describe('the browser extra', () => {
  type Call = { command: string; args: string[]; env: Record<string, string> };
  /** Records installer commands instead of running them; `--version` answers like a working browser unless it is `broken`. */
  function recorder(o: { failPlaywright?: boolean; broken?: string[] } = {}): { calls: Call[]; exec: Exec } {
    const calls: Call[] = [];
    const exec: Exec = async (command, args, opts) => {
      calls.push({ command, args, env: opts.env });
      if (args[0] === '--version') return o.broken?.includes(command) ? { code: 137, stdout: '', stderr: 'Killed: 9' } : { code: 0, stdout: `${basename(command)} 140.0.7339.80\n`, stderr: '' };
      if (o.failPlaywright && args[1] === 'playwright') return { code: 1, stdout: '', stderr: 'Downloading Chromium\nerror: connect ETIMEDOUT' };
      return { code: 0, stdout: '', stderr: '' };
    };
    return { calls, exec };
  }
  /** A file standing in for an installed browser's executable (a ready runtime checks that it is still there). */
  function installed(name: string): InstalledBrowser {
    const path = join(h.dir, 'apps', name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, '#!/bin/sh\n');
    chmodSync(path, 0o755);
    return { name, path };
  }
  const steps = (calls: Call[]) => calls.map((c) => `${basename(c.command)} ${c.args[0]}`);
  const browserEntry = entry({ python: { version: '3.12', packages: ['playwright==1.55.0'] }, extras: ['browser'] });

  it('uses an installed Chrome or Edge that starts: DESK_BROWSER in the environment and nothing downloaded', async () => {
    process.env.DESK_OPENAI_API_KEY = 'sk-should-not-leak';
    const chrome = installed('Google Chrome');
    const { calls, exec } = recorder();
    const r = make({ uv: '/stub/uv', exec, findBrowsers: () => [chrome] });
    r.setup(ref, browserEntry, '2026-09-24');
    await r.settled(ref);
    expect(r.state(ref)).toEqual({ state: 'ready', reason: null });
    expect(steps(calls)).toEqual(['uv venv', 'uv pip', 'Google Chrome --version']);
    expect(calls[2]!.env.DESK_OPENAI_API_KEY).toBeUndefined();
    const env = r.env(ref);
    expect(env.vars.DESK_BROWSER).toBe(chrome.path);
    expect(env.vars.PLAYWRIGHT_BROWSERS_PATH).toBeUndefined();
    expect(existsSync(join(r.dir(ref), 'browsers'))).toBe(false);
    expect(env.note).toMatch(/a browser for Playwright: the installed Google Chrome \(its executable is in DESK_BROWSER/);
    const saved = JSON.parse(readFileSync(join(r.dir(ref), 'desk-env.json'), 'utf8')) as { vars: Record<string, string> };
    expect(saved.vars.DESK_BROWSER).toBe(chrome.path);
  });

  it('skips a browser that does not start, and downloads Chromium when none does', async () => {
    const chrome = installed('Google Chrome');
    const edge = installed('Microsoft Edge');
    const first = recorder({ broken: [chrome.path] });
    const r = make({ uv: '/stub/uv', exec: first.exec, findBrowsers: () => [chrome, edge] });
    r.setup(ref, browserEntry, '2026-09-24');
    await r.settled(ref);
    expect(steps(first.calls)).toEqual(['uv venv', 'uv pip', 'Google Chrome --version', 'Microsoft Edge --version']);
    expect(r.env(ref).vars.DESK_BROWSER).toBe(edge.path);
    expect(r.env(ref).note).toContain('the installed Microsoft Edge');

    const none = recorder({ broken: [chrome.path, edge.path] });
    const r2 = make({ uv: '/stub/uv', exec: none.exec, findBrowsers: () => [chrome, edge] });
    r2.setup(ref, browserEntry, '2026-09-24');
    await r2.settled(ref);
    expect(r2.state(ref)).toEqual({ state: 'ready', reason: null });
    expect(steps(none.calls)).toEqual(['uv venv', 'uv pip', 'Google Chrome --version', 'Microsoft Edge --version', 'python -m']);
    expect(r2.env(ref).vars).toMatchObject({ PLAYWRIGHT_BROWSERS_PATH: join(r2.dir(ref), 'browsers') });
    expect(r2.env(ref).vars.DESK_BROWSER).toBeUndefined();
  });

  it('does not start the browser on Windows, where chrome.exe --version opens a window', async () => {
    const edge = installed('msedge.exe');
    const { calls, exec } = recorder();
    const r = make({ uv: '/stub/uv', exec, platform: 'win32', findBrowsers: () => [edge] });
    r.setup(ref, browserEntry, '2026-09-24');
    await r.settled(ref);
    expect(steps(calls)).toEqual(['uv venv', 'uv pip']);
    expect(r.env(ref).vars.DESK_BROWSER).toBe(edge.path);
  });

  it('reports a ready runtime as failed once its browser is gone, and a retry falls back to Chromium', async () => {
    const chrome = installed('Google Chrome');
    let found = [chrome];
    const { calls, exec } = recorder();
    const r = make({ uv: '/stub/uv', exec, findBrowsers: () => found });
    r.setup(ref, browserEntry, '2026-09-24');
    await r.settled(ref);
    expect(r.state(ref).state).toBe('ready');

    rmSync(chrome.path);
    found = [];
    expect(r.state(ref)).toEqual({ state: 'failed', reason: `The browser this skill used is no longer installed (${chrome.path}). Retry to find another one or download Chromium.` });
    expect(r.env(ref)).toMatchObject({ state: 'failed', bins: [], vars: {}, note: null });
    writeFileSync(join(r.dir(ref), 'desk-env.json'), '{"bins": [');
    expect(r.state(ref)).toEqual({ state: 'failed', reason: 'The environment is missing from disk. Retry to rebuild it.' });

    r.setup(ref, browserEntry, '2026-09-24');
    await r.settled(ref);
    expect(r.state(ref)).toEqual({ state: 'ready', reason: null });
    expect(steps(calls).at(-1)).toBe('python -m');
    expect(r.env(ref).note).toContain('Chromium, downloaded by Desk');
  });

  it('downloads Chromium into the environment with Playwright when no browser is installed, like playwright-chromium', async () => {
    process.env.DESK_OPENAI_API_KEY = 'sk-should-not-leak';
    const { calls, exec } = recorder();
    const r = make({ uv: '/stub/uv', exec, findBrowsers: () => [] });
    r.setup(ref, browserEntry, '2026-09-24');
    await r.settled(ref);
    expect(r.state(ref)).toEqual({ state: 'ready', reason: null });
    const dir = r.dir(ref);
    expect(calls).toHaveLength(3);
    expect(calls[2]).toMatchObject({ command: join(dir, 'py', 'bin', 'python'), args: ['-m', 'playwright', 'install', 'chromium'], env: { PLAYWRIGHT_BROWSERS_PATH: join(dir, 'browsers') } });
    expect(calls[2]!.env.DESK_OPENAI_API_KEY).toBeUndefined();
    const env = r.env(ref);
    expect(env.vars.PLAYWRIGHT_BROWSERS_PATH).toBe(join(dir, 'browsers'));
    expect(env.vars.DESK_BROWSER).toBeUndefined();
    expect(env.note).toContain('a browser for Playwright: Chromium, downloaded by Desk');
  });

  it('records the Playwright error when the download fails, and refuses an entry without Python', async () => {
    const { exec } = recorder({ failPlaywright: true });
    const r = make({ uv: '/stub/uv', exec, findBrowsers: () => [] });
    r.setup(ref, browserEntry, '2026-09-24');
    await r.settled(ref);
    expect(r.state(ref)).toEqual({ state: 'failed', reason: expect.stringMatching(/python -m failed: .*ETIMEDOUT/s) });

    const { calls, exec: exec2 } = recorder();
    const noPython = make({ uv: '/stub/uv', exec: exec2, findBrowsers: () => [installed('Google Chrome')] });
    noPython.setup(ref, entry({ extras: ['browser'] }), '2026-09-24');
    await noPython.settled(ref);
    expect(noPython.state(ref)).toEqual({ state: 'failed', reason: 'browser needs the Python runtime with playwright' });
    expect(calls).toEqual([]);
  });

  it('looks for browsers on this machine by default', async () => {
    const bin = join(h.dir, 'path-bin');
    mkdirSync(bin, { recursive: true });
    writeFileSync(join(bin, 'chromium'), '#!/bin/sh\n');
    chmodSync(join(bin, 'chromium'), 0o755);
    const saved = process.env.PATH;
    process.env.PATH = bin;
    try {
      const { exec } = recorder();
      const r = make({ uv: '/stub/uv', exec, platform: 'linux' });
      r.setup(ref, browserEntry, '2026-09-24');
      await r.settled(ref);
      expect(r.env(ref).vars.DESK_BROWSER).toBe(join(bin, 'chromium'));
    } finally {
      process.env.PATH = saved;
    }
  });

  it('describes both browser extras in the runtime note', () => {
    const both = entry({ python: { version: '3.12', packages: ['playwright==1.55.0'] }, extras: ['playwright-chromium', 'browser'] });
    expect(runtimeNote(both, { name: 'Google Chrome', path: '/x' })).toContain(
      'a Chromium browser for Playwright; a browser for Playwright: the installed Google Chrome (its executable is in DESK_BROWSER',
    );
    expect(runtimeNote(both)).toContain('a Chromium browser for Playwright; a browser for Playwright: Chromium, downloaded by Desk');
  });
});

describe('skill runtimes in tools', () => {
  let rt: Runtime;
  afterEach(async () => rt?.shutdown());

  it('puts a ready runtime on PATH for skill_run, refuses a failed one, and drops it when the skill is deleted', async () => {
    const r = make({ uv: uvStub().uv });
    rt = newRuntime(h, { skillEnv: r });
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    const desk = getDeskAgent(h.store.db, projectId)!;
    rt.saveSkill({ scope: 'global', name: 'tool', description: 'Tool', instructions: 'x', files: [{ path: 'scripts/which.sh', content: 'command -v python\necho "$VIRTUAL_ENV"\n' }] });
    const ctx = testToolContext(desk.workspace_path!, { projectId, agentId: desk.id, services: rt.services });
    const call = (input: unknown) => executeToolCall([skillRunTool], { id: 'c', name: 'skill_run', arguments: JSON.stringify(input) }, ctx);

    r.setup(ref, entry({ python: { version: '3.12', packages: [] } }), '2026-09-24');
    await r.settled(ref);
    const read = await executeToolCall([skillReadTool], { id: 'r', name: 'skill_read', arguments: JSON.stringify({ name: 'tool' }) }, ctx);
    expect(read.content).toContain("Runtime: Desk set up this skill's runtime: Python 3.12 (run scripts with python3).");
    const ok = await call({ name: 'tool', script: 'which.sh' });
    expect(ok.content).toContain(`${r.dir(ref)}/py/bin/python`);
    expect(ok.content).toContain(`${r.dir(ref)}/py`);

    const failing = make({ uv: uvStub(true).uv });
    failing.setup(ref, entry({ python: { version: '3.12', packages: ['x==1'] } }), '2026-09-24');
    await failing.settled(ref);
    const refused = await call({ name: 'tool', script: 'which.sh' });
    expect(refused).toMatchObject({ status: 'error', content: expect.stringMatching(/tool's runtime is not ready: .*no wheel/) });

    rt.deleteSkill('global', 'tool');
    expect(r.state(ref)).toEqual({ state: 'none', reason: null });
    expect(withSkillEnv(scrubbedEnv('/w', { PATH: '/usr/bin' }), rt.skillEnv(desk.id)).PATH).toBe('/usr/bin');
  });

  it('gives shell tools the variables of the skill whose environment comes first on PATH', () => {
    const ready = (name: string, vars: Record<string, string>): SkillEnv => ({ state: 'ready', reason: null, bins: [`/${name}/bin`], vars, note: null });
    const envs: Record<string, SkillEnv> = {
      testing: ready('testing', { VIRTUAL_ENV: '/testing/py', PLAYWRIGHT_BROWSERS_PATH: '/testing/browsers' }),
      research: ready('research', { VIRTUAL_ENV: '/research/py', PLAYWRIGHT_BROWSERS_PATH: '/research/browsers', DESK_BROWSER: '/chrome' }),
    };
    rt = newRuntime(h, { skillEnv: { env: (r) => envs[r.name]!, remove: () => {} } });
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    const desk = getDeskAgent(h.store.db, projectId)!;
    for (const name of Object.keys(envs)) rt.saveSkill({ scope: 'global', name, description: name, instructions: 'x' });
    rt.activateSkills(desk.id, ['testing', 'research']);
    // The first skill's python runs, so its Playwright must find its own browsers, not the second skill's.
    expect(rt.skillEnv(desk.id)).toMatchObject({
      bins: ['/testing/bin', '/research/bin'],
      vars: { VIRTUAL_ENV: '/testing/py', PLAYWRIGHT_BROWSERS_PATH: '/testing/browsers', DESK_BROWSER: '/chrome' },
    });
  });
});
