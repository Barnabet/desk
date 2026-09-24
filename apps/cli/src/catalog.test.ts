import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { startFakeModel, text, type FakeModelServer } from '@desk/fake-model';
import { treeDigest } from '@desk/core';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import type { CatalogEntry } from '@desk/protocol';
import { runCli } from './commands';

let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;

const md = Buffer.from('---\nname: word-documents\ndescription: Create and edit .docx files.\n---\n\nRun scripts/docx_read.py.\n');
const script = Buffer.from('print("docx")\n');
const entry = (): CatalogEntry => ({
  id: 'word-documents',
  title: 'Word documents',
  category: 'documents',
  summary: 'Create, read and edit .docx files.',
  license: 'MIT',
  homepage: 'https://github.com/Barnabet/desk',
  source: { type: 'builtin', path: 'word-documents' },
  digest: treeDigest([
    { path: 'SKILL.md', content: md },
    { path: 'scripts/docx_read.py', content: script },
  ]),
  files: 2,
  bytes: md.length + script.length,
  scripts: 1,
  runtime: { python: { version: '3.12', packages: ['python-docx==1.2.0'] } },
  caveats: ['Tracked changes are kept but not shown.'],
});

beforeEach(async () => {
  dir = realpathSync(mkdtempSync(join(tmpdir(), 'desk-cli-catalog-')));
  const builtin = join(dir, 'builtin', 'word-documents');
  mkdirSync(join(builtin, 'scripts'), { recursive: true });
  writeFileSync(join(builtin, 'SKILL.md'), md);
  writeFileSync(join(builtin, 'scripts', 'docx_read.py'), script);
  fake = await startFakeModel(() => text('ok'));
  daemon = await startDaemon({
    dataDir: join(dir, 'data'),
    port: 0,
    modelConfig: { baseURL: fake.url, apiKey: 'k' },
    sandboxAvailable: false,
    catalog: { builtinRoot: join(dir, 'builtin'), file: { version: 1, updated: '2026-09-24', entries: [entry()] } },
    runtimes: { uv: null },
  });
});
afterEach(async () => {
  await daemon.stop();
  await fake.close();
  rmSync(dir, { recursive: true, force: true });
});

async function cli(argv: string[], confirm?: (q: string) => Promise<boolean>) {
  let out = '';
  let err = '';
  const code = await runCli(argv, { out: (s) => (out += s), err: (s) => (err += s), dataDir: join(dir, 'data'), ...(confirm ? { confirm } : {}) });
  return { code, out, err };
}

describe('desk catalog', () => {
  it('lists, shows the review, and installs only after confirmation', async () => {
    const listed = await cli(['catalog']);
    expect(listed.out).toContain('Documents & data\n  word-documents');
    expect(listed.out).toContain('Create, read and edit .docx files.');
    expect(listed.out).not.toContain('[installed');

    const shown = (await cli(['catalog', 'show', 'word-documents'])).out;
    expect(shown).toContain('Word documents (word-documents) — MIT');
    expect(shown).toContain('Source:   Desk (first-party, shipped with the app)');
    expect(shown).toContain('Sets up:  Python 3.12 (python-docx==1.2.0)');
    expect(shown).toContain('  - Tracked changes are kept but not shown.');
    expect(shown).toMatch(/ {2}scripts\/docx_read\.py {2}\d+ B {2}script/);

    const noPrompt = await cli(['catalog', 'install', 'word-documents']);
    expect(noPrompt.code).toBe(1);
    expect(noPrompt.err).toContain('Pass --yes');

    const asked: string[] = [];
    const declined = await cli(['catalog', 'install', 'word-documents'], async (q) => (asked.push(q), false));
    expect(asked).toEqual(['Install word-documents for every project?']);
    expect(declined.out).toContain('Not installed.');

    const installed = await cli(['catalog', 'install', 'word-documents', '--yes']);
    expect(installed.out).toContain('Installed word-documents globally (v1). Desk is setting up Python 3.12');
    // Without uv the runtime fails (in the background); the listing says so.
    let listing = '';
    for (let i = 0; i < 100 && !listing.includes('setup failed'); i++) listing = (await cli(['catalog'])).out;
    expect(listing).toContain('[installed · setup failed]');

    await cli(['project', 'new', 'Thesis', '--goal', 'g']);
    expect((await cli(['catalog', 'install', 'word-documents', '-p', 'Thesis', '-y'])).out).toContain('Installed word-documents in Thesis');
    expect((await cli(['catalog'])).out).toContain('; in Thesis]');
  });
});
