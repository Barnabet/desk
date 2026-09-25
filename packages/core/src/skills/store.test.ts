import { link, mkdir, mkdtemp, readFile, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { sandboxGuard } from '../tools/sandbox';
import { parseSkillMd, SkillStore } from './store';

let dir: string;
let store: SkillStore;
beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), 'desk-skills-'));
  store = new SkillStore(dir);
});
afterEach(async () => rm(dir, { recursive: true, force: true }));

const basic = { name: 'weekly-report', description: 'Builds the weekly report. Use when asked for a weekly summary.', instructions: '1. Run scripts/collect.sh\n2. Summarise.' };

describe('SkillStore', () => {
  it("never copies Desk's secrets out of a draft, even hard-linked into it", async () => {
    const secret = join(dir, 'daemon.json');
    await writeFile(secret, '{"token":"tok-123"}');
    const guarded = new SkillStore(dir, sandboxGuard({ dataDir: dir, secrets: [secret] }));
    const draft = join(dir, 'draft', 'leaky');
    await mkdir(draft, { recursive: true });
    await writeFile(join(draft, 'SKILL.md'), '---\nname: leaky\ndescription: Leaks\n---\n\nx\n');
    await link(secret, join(draft, 'token.json'));
    expect(() => guarded.save({ scope: 'project', projectId: 'p1', name: 'leaky', fromDir: draft })).toThrow(/holds Desk credentials/);
    expect(guarded.get('project', 'leaky', 'p1')).toBeUndefined();
  });

  it('creates SKILL.md with frontmatter and executable scripts', async () => {
    const r = store.save({ scope: 'global', ...basic, files: [{ path: 'scripts/collect.sh', content: '#!/bin/sh\necho hi\n' }, { path: 'references/format.md', content: '# Format' }] });
    expect(r).toMatchObject({ version: 1, created: true });
    const md = parseSkillMd(await readFile(join(r.dir, 'SKILL.md'), 'utf8'));
    expect(md.frontmatter).toMatchObject({ name: 'weekly-report', description: basic.description });
    expect(md.instructions).toContain('scripts/collect.sh');
    expect((await stat(join(r.dir, 'scripts/collect.sh'))).mode & 0o111).toBeTruthy();
    expect((await stat(join(r.dir, 'references/format.md'))).mode & 0o111).toBe(0);
    const detail = store.get('global', 'weekly-report')!;
    expect(detail.files.map((f) => f.path)).toEqual(['SKILL.md', 'references/format.md', 'scripts/collect.sh']);
  });

  it('reads any past version with its instructions and files', async () => {
    store.save({ scope: 'global', ...basic, instructions: 'first', files: [{ path: 'notes.md', content: 'v1 notes' }] });
    store.save({ scope: 'global', ...basic, instructions: 'second' });
    const v1 = store.getVersion('global', 'weekly-report', 1)!;
    expect(v1).toMatchObject({ version: 1, instructions: 'first', description: basic.description });
    expect(v1.files.map((f) => f.path)).toContain('notes.md');
    expect(await readFile(store.filePath(v1, 'notes.md'), 'utf8')).toBe('v1 notes');
    expect(store.getVersion('global', 'weekly-report', 2)).toMatchObject({ version: 2, instructions: 'second' });
    expect(store.getVersion('global', 'weekly-report', 3)).toBeUndefined();
    expect(() => store.getVersion('global', '../x', 1)).toThrow();
  });

  it('project skills shadow global ones', () => {
    store.save({ scope: 'global', ...basic });
    store.save({ scope: 'global', name: 'other', description: 'Other skill', instructions: 'x' });
    store.save({ scope: 'project', projectId: 'p1', ...basic, description: 'Project flavour' });
    expect(store.list('p1').map((s) => [s.name, s.scope])).toEqual([['other', 'global'], ['weekly-report', 'project']]);
    expect(store.resolve('weekly-report', 'p1')?.description).toBe('Project flavour');
    expect(store.resolve('weekly-report')?.scope).toBe('global');
    expect(store.list().map((s) => s.name)).toEqual(['other', 'weekly-report']);
  });

  it('updates keep history and restore brings an old version back as the newest', async () => {
    store.save({ scope: 'global', ...basic, files: [{ path: 'scripts/a.sh', content: 'echo 1' }] });
    const v2 = store.save({ scope: 'global', name: basic.name, instructions: 'New steps', removeFiles: ['scripts/a.sh'] });
    expect(v2).toMatchObject({ version: 2, created: false, description: basic.description });
    expect(store.get('global', basic.name)!.files.map((f) => f.path)).toEqual(['SKILL.md']);
    expect(store.history('global', basic.name).map((h) => [h.version, h.current])).toEqual([[1, false], [2, true]]);
    const v3 = store.restore('global', basic.name, 1);
    expect(v3.version).toBe(3);
    expect(store.get('global', basic.name)!.instructions).toContain('collect.sh');
    expect(await readFile(join(v3.dir, 'scripts/a.sh'), 'utf8')).toBe('echo 1');
    store.delete('global', basic.name);
    expect(store.resolve(basic.name)).toBeUndefined();
    expect(store.restore('global', basic.name, 3).version).toBe(4);
  });

  it('rejects invalid names, escaping paths, missing descriptions and oversized files', () => {
    expect(() => store.save({ scope: 'global', ...basic, name: 'Bad Name' })).toThrow(/Invalid skill name/);
    expect(() => store.save({ scope: 'global', ...basic, files: [{ path: '../evil.sh', content: 'x' }] })).toThrow(/Invalid skill file path/);
    expect(() => store.save({ scope: 'global', ...basic, files: [{ path: '/etc/x', content: 'x' }] })).toThrow(/Invalid skill file path/);
    expect(() => store.save({ scope: 'global', ...basic, description: ' ' })).toThrow(/description/);
    expect(() => store.save({ scope: 'global', name: 'x', description: 'd' })).toThrow(/instructions/);
    expect(() => store.save({ scope: 'global', ...basic, files: [{ path: 'big.bin', content: Buffer.alloc(3 * 1024 * 1024) }] })).toThrow(/larger/);
    expect(() => store.save({ scope: 'project', ...basic })).toThrow(/project id/);
    expect(store.list()).toEqual([]);
  });

  it('installs from a draft directory (Claude Code style), skipping symlinks', async () => {
    const draft = join(dir, 'draft', 'csv-clean');
    await mkdir(join(draft, 'scripts'), { recursive: true });
    await writeFile(join(draft, 'SKILL.md'), '---\nname: csv-clean\ndescription: Cleans CSV files\nlicense: MIT\n---\n\nRun scripts/clean.py <file>\n');
    await writeFile(join(draft, 'scripts', 'clean.py'), 'print("ok")\n');
    await symlink('/etc/passwd', join(draft, 'passwd'));
    expect(SkillStore.declaredName(draft)).toBe('csv-clean');
    const r = store.save({ scope: 'project', projectId: 'p1', name: 'csv-clean', fromDir: draft });
    const detail = store.get('project', 'csv-clean', 'p1')!;
    expect(detail.frontmatter).toMatchObject({ license: 'MIT', description: 'Cleans CSV files' });
    expect(detail.files.map((f) => f.path)).toEqual(['SKILL.md', 'scripts/clean.py']);
    expect((await stat(join(r.dir, 'scripts/clean.py'))).mode & 0o111).toBeTruthy();
  });

  it('lists hand-edited and malformed skills without crashing', async () => {
    const root = store.root('global');
    await mkdir(join(root, 'hand-made'), { recursive: true });
    await writeFile(join(root, 'hand-made', 'SKILL.md'), '---\nname: hand-made\ndescription: Made by hand\n---\nDo it.\n');
    await mkdir(join(root, 'broken'), { recursive: true });
    await writeFile(join(root, 'broken', 'SKILL.md'), 'no frontmatter');
    await mkdir(join(root, 'empty'), { recursive: true });
    const list = store.list();
    expect(list.find((s) => s.name === 'hand-made')).toMatchObject({ description: 'Made by hand', version: 1 });
    expect(list.find((s) => s.name === 'broken')?.error).toMatch(/frontmatter/);
    expect(list.find((s) => s.name === 'empty')?.error).toBe('SKILL.md is missing');
  });

  it('filePath refuses to leave the skill', async () => {
    const r = store.save({ scope: 'global', ...basic });
    await symlink('/etc', join(r.dir, 'out'));
    const skill = store.resolve(basic.name)!;
    expect(store.filePath(skill, 'SKILL.md')).toMatch(/SKILL\.md$/);
    expect(() => store.filePath(skill, 'out/passwd')).toThrow(/outside/);
    expect(() => store.filePath(skill, '../x')).toThrow(/Invalid/);
  });
});
