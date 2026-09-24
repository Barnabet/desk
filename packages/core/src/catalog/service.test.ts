import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { CatalogEntry, CatalogFile } from '@desk/protocol';
import type { Runtime } from '../runtime/runtime';
import { createHarness, newRuntime, type Harness } from '../testing/harness';
import { chunked, makeTarball } from '../testing/tarball';
import { treeDigest } from './digest';
import { CatalogService, type CatalogFetch } from './service';

const SHA1 = '1'.repeat(40);
const SHA2 = '2'.repeat(40);
const md = (name: string, desc = 'Search scholarly APIs.') => `---\nname: ${name}\ndescription: ${desc}\n---\n\nUse scripts/lookup.py.\n`;

const v1 = [
  { path: 'SKILL.md', content: Buffer.from(md('paper-lookup')) },
  { path: 'scripts/lookup.py', content: Buffer.from('print("v1")\n') },
];
const v2 = [v1[0]!, { path: 'scripts/lookup.py', content: Buffer.from('print("v2")\n') }];

const tarOf = (files: typeof v1, extra: Parameters<typeof makeTarball>[1] = []) =>
  makeTarball('scientific-agent-skills-x', [
    { name: 'LICENSE', content: 'MIT License\n\nCopyright (c) K-Dense' },
    ...files.map((f) => ({ name: `skills/paper-lookup/${f.path}`, content: f.content, mode: f.path.endsWith('.py') ? 0o755 : 0o644 })),
    ...extra,
  ]);

const entryFor = (sha: string, files: typeof v1): CatalogEntry => ({
  id: 'paper-lookup',
  title: 'Paper lookup',
  category: 'research',
  summary: 'Search scholarly APIs.',
  license: 'MIT',
  homepage: 'https://github.com/K-Dense-AI/scientific-agent-skills',
  source: { type: 'github', repo: 'K-Dense-AI/scientific-agent-skills', path: 'skills/paper-lookup', sha },
  digest: treeDigest(files),
  files: files.length,
  bytes: 100,
  runtime: {},
  caveats: [],
});

let h: Harness;
let runtime: Runtime;
let archives: Map<string, Buffer>;
let fetched: string[];
const fetchFake: CatalogFetch = async (url) => {
  fetched.push(url);
  const buf = archives.get(url);
  if (!buf) throw new Error(`404 ${url}`);
  return chunked(buf);
};
const service = (entries: CatalogEntry[]) => {
  const catalog: CatalogFile = { version: 1, updated: '2026-09-24', entries };
  return new CatalogService({ runtime, store: h.store, dataDir: h.dir, catalog, builtinRoot: join(h.dir, 'builtin'), fetch: fetchFake, archiveBase: 'https://codeload.test' });
};
const url = (sha: string) => `https://codeload.test/K-Dense-AI/scientific-agent-skills/tar.gz/${sha}`;

beforeEach(async () => {
  h = await createHarness();
  runtime = newRuntime(h);
  archives = new Map([
    [url(SHA1), tarOf(v1)],
    [url(SHA2), tarOf(v2)],
  ]);
  fetched = [];
});
afterEach(async () => h.cleanup());

describe('CatalogService', () => {
  it('prepares a review from the pinned archive: files, scripts, SKILL.md, the repo licence and warnings', async () => {
    const c = service([entryFor(SHA1, v1)]);
    const review = await c.prepare('paper-lookup');
    expect(fetched).toEqual([url(SHA1)]);
    expect(review.source_url).toBe(`https://github.com/K-Dense-AI/scientific-agent-skills/tree/${SHA1}/skills/paper-lookup`);
    expect(review.files).toEqual([
      { path: 'SKILL.md', size: v1[0]!.content.length, script: false },
      { path: 'scripts/lookup.py', size: v1[1]!.content.length, script: true },
    ]);
    expect(review.skill_md).toContain('name: paper-lookup');
    expect(review.license_text).toMatch(/^MIT License/);
    expect(review.warnings).toEqual([]);
  });

  it('refuses an archive that does not match the digest, and installs nothing', async () => {
    archives.set(url(SHA1), tarOf(v2));
    const c = service([entryFor(SHA1, v1)]);
    await expect(c.install('paper-lookup')).rejects.toThrow(/does not match the catalog/);
    expect(runtime.skills.get('global', 'paper-lookup')).toBeUndefined();
    expect(c.list()[0]!.installs).toEqual([]);
  });

  it('refuses a SKILL.md that names another skill', async () => {
    const wrong = [{ path: 'SKILL.md', content: Buffer.from(md('other-name')) }];
    archives.set(url(SHA1), tarOf(wrong));
    await expect(service([entryFor(SHA1, wrong)]).prepare('paper-lookup')).rejects.toThrow(/names itself "other-name"/);
  });

  it('installs, detects an update, protects local edits, and records catalog origins in history', async () => {
    let c = service([entryFor(SHA1, v1)]);
    const res = await c.install('paper-lookup');
    expect(res).toEqual({ skill: { name: 'paper-lookup', scope: 'global', version: 1, project_id: null }, state: 'installed', runtime: 'none' });
    const skill = runtime.skills.get('global', 'paper-lookup')!;
    expect(readFileSync(join(skill.dir, 'scripts', 'lookup.py'), 'utf8')).toBe('print("v1")\n');
    expect(readFileSync(join(skill.dir, 'LICENSE'), 'utf8')).toMatch(/^MIT License/);
    expect(c.list()[0]!.installs).toEqual([{ scope: 'global', project_id: null, state: 'installed', sha: SHA1, runtime: 'none', runtime_reason: null }]);

    c = service([entryFor(SHA2, v2)]);
    expect(c.list()[0]!.installs[0]!.state).toBe('update_available');
    await c.install('paper-lookup');
    expect(readFileSync(join(runtime.skills.get('global', 'paper-lookup')!.dir, 'scripts', 'lookup.py'), 'utf8')).toBe('print("v2")\n');
    expect(c.list()[0]!.installs[0]).toMatchObject({ state: 'installed', sha: SHA2 });

    runtime.saveSkill({ scope: 'global', name: 'paper-lookup', instructions: 'My own edit.' });
    expect(c.list()[0]!.installs[0]).toMatchObject({ state: 'modified', sha: SHA2 });
    await expect(c.install('paper-lookup')).rejects.toThrow(/was edited after it was installed/);
    await c.install('paper-lookup', { replace_modified: true });
    expect(c.list()[0]!.installs[0]!.state).toBe('installed');

    expect(runtime.skillHistory('global', 'paper-lookup').map((v) => [v.version, v.origin, v.change_note])).toEqual([
      [1, `catalog:paper-lookup@${SHA1}`, 'Installed from the catalog (K-Dense-AI/scientific-agent-skills@1111111)'],
      [2, `catalog:paper-lookup@${SHA2}`, 'Updated from the catalog (K-Dense-AI/scientific-agent-skills@2222222)'],
      [3, 'user', 'Updated'],
      [4, `catalog:paper-lookup@${SHA2}`, 'Updated from the catalog (K-Dense-AI/scientific-agent-skills@2222222)'],
    ]);
  });

  it('never overwrites a skill of the same name that did not come from the catalog', async () => {
    runtime.saveSkill({ scope: 'global', name: 'paper-lookup', description: 'Mine', instructions: 'Mine.' });
    const c = service([entryFor(SHA1, v1)]);
    expect(c.list()[0]!.installs).toEqual([{ scope: 'global', project_id: null, state: 'name_taken', sha: null, runtime: 'none', runtime_reason: null }]);
    await expect(c.install('paper-lookup')).rejects.toThrow(/did not come from the catalog/);
  });

  it('installs into a project, and forgets an install once the skill is deleted', async () => {
    const projectId = runtime.createProject({ name: 'Thesis', goal: 'g' });
    const c = service([entryFor(SHA1, v1)]);
    await expect(c.install('paper-lookup', { scope: 'project' })).rejects.toThrow(/needs project_id/);
    await c.install('paper-lookup', { scope: 'project', project_id: projectId });
    expect(c.list()[0]!.installs).toEqual([{ scope: 'project', project_id: projectId, state: 'installed', sha: SHA1, runtime: 'none', runtime_reason: null }]);
    runtime.deleteSkill('project', 'paper-lookup', projectId);
    expect(c.list()[0]!.installs).toEqual([]);
  });

  it('installs builtin skills from disk, pinned by digest', async () => {
    const files = [{ path: 'SKILL.md', content: Buffer.from(md('word-documents', 'Create and edit .docx files.')) }];
    const dir = join(h.dir, 'builtin', 'word-documents');
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'SKILL.md'), files[0]!.content);
    const entry: CatalogEntry = { ...entryFor(SHA1, files), id: 'word-documents', source: { type: 'builtin', path: 'word-documents' } };
    const c = service([entry]);
    expect((await c.prepare('word-documents')).source_url).toBeNull();
    await c.install('word-documents');
    expect(c.list()[0]!.installs[0]).toMatchObject({ state: 'installed', sha: `builtin-${entry.digest.slice(7, 19)}` });
    expect(fetched).toEqual([]);
  });

  it('reports unknown entries as not found', async () => {
    await expect(service([]).prepare('nope')).rejects.toThrow(/Unknown catalog skill: nope/);
  });
});
