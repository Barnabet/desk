import { existsSync, mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import type { InputSpec } from '@desk/protocol';
import { ValidationError } from '../errors';
import { sandboxGuard } from '../tools/sandbox';
import { automationHarness, waitDef } from '../testing/automations';
import type { Harness } from '../testing/harness';
import { ensureStepDir, inputsDir, logFile, prepareRunFolder, pruneRuns, resolveInputs, runDir, stepDir } from './folders';

let h: Harness;
afterEach(async () => h?.cleanup());

const specs: InputSpec[] = [
  { key: 'topic', label: 'Topic', type: 'text', required: true },
  { key: 'count', label: 'Count', type: 'number', required: false, default: 3 },
  { key: 'draft', label: 'Draft only', type: 'boolean', required: false },
  { key: 'site', label: 'Site', type: 'url', required: false },
  { key: 'tone', label: 'Tone', type: 'choice', required: false, options: ['formal', 'casual'] },
  { key: 'doc', label: 'Document', type: 'file', required: false },
  { key: 'dir', label: 'Folder', type: 'folder', required: false },
];

describe('run inputs', () => {
  it('applies defaults, coerces CLI strings and refuses what does not fit', () => {
    expect(resolveInputs(specs, { topic: 'ai' })).toEqual({ topic: 'ai', count: 3 });
    expect(resolveInputs(specs, { topic: 'ai', count: '7', draft: 'true', tone: 'casual' })).toEqual({ topic: 'ai', count: 7, draft: true, tone: 'casual' });
    const refuse = (given: Record<string, string | number | boolean>) => expect(() => resolveInputs(specs, given)).toThrow(ValidationError);
    refuse({});
    refuse({ topic: '' });
    refuse({ topic: 'ai', count: 'many' });
    refuse({ topic: 'ai', draft: 'yes' });
    refuse({ topic: 'ai', tone: 'angry' });
    refuse({ topic: 'ai', site: 'file:///etc/passwd' });
    refuse({ topic: 'ai', nope: 1 });
  });
});

describe('run folders', () => {
  it('lays out the folders and copies file and folder inputs', async () => {
    ({ h } = await automationHarness());
    const guard = sandboxGuard({ dataDir: h.dir, secrets: [join(h.files, 'secret.txt')] });
    writeFileSync(join(h.files, 'report.pdf'), 'pdf');
    mkdirSync(join(h.files, 'photos', 'sub'), { recursive: true });
    writeFileSync(join(h.files, 'photos', 'a.jpg'), 'a');
    writeFileSync(join(h.files, 'photos', 'sub', 'b.jpg'), 'b');
    symlinkSync('/etc/hosts', join(h.files, 'photos', 'link'));
    const values = await prepareRunFolder({ dataDir: h.dir, runId: 'r1', specs, inputs: { topic: 'ai', doc: join(h.files, 'report.pdf'), dir: join(h.files, 'photos') }, guard });
    expect(values.doc).toBe(join(inputsDir(h.dir, 'r1'), 'doc', 'report.pdf'));
    expect(readFileSync(values.doc as string, 'utf8')).toBe('pdf');
    expect(existsSync(join(values.dir as string, 'sub', 'b.jpg'))).toBe(true);
    expect(existsSync(join(values.dir as string, 'link'))).toBe(false); // symlinks are skipped
    expect(JSON.parse(readFileSync(join(runDir(h.dir, 'r1'), 'inputs.json'), 'utf8'))).toEqual(values);
    expect(ensureStepDir(h.dir, 'r1', 'fetch')).toBe(stepDir(h.dir, 'r1', 'fetch'));
    expect(existsSync(join(stepDir(h.dir, 'r1', 'fetch'), '.desk'))).toBe(true);
    expect(existsSync(join(runDir(h.dir, 'r1'), 'logs'))).toBe(true);
    // Logs live outside the step folder: a script can write its folder, so a symlink planted there could redirect deskd.
    expect(logFile(h.dir, 'r1', 'fetch')).toBe(join(runDir(h.dir, 'r1'), 'logs', 'fetch.txt'));
  });

  it('refuses secrets, the data folder, symlinks, relative paths and oversized inputs', async () => {
    ({ h } = await automationHarness());
    writeFileSync(join(h.files, 'secret.txt'), 's');
    writeFileSync(join(h.files, 'big.bin'), Buffer.alloc(2048));
    symlinkSync(join(h.files, 'big.bin'), join(h.files, 'alias.bin'));
    const guard = sandboxGuard({ dataDir: h.dir, secrets: [join(h.files, 'secret.txt')] });
    const run = (doc: string, maxBytes?: number) => prepareRunFolder({ dataDir: h.dir, runId: 'r2', specs, inputs: { topic: 'x', doc }, guard, ...(maxBytes ? { maxBytes } : {}) });
    await expect(run(join(h.files, 'secret.txt'))).rejects.toThrow(ValidationError);
    mkdirSync(join(h.dir, 'projects'), { recursive: true });
    writeFileSync(join(h.dir, 'projects', 'x.txt'), 'x');
    await expect(run(join(h.dir, 'projects', 'x.txt'))).rejects.toThrow(/Desk's data folder/);
    await expect(run(join(h.files, 'alias.bin'))).rejects.toThrow(ValidationError);
    await expect(run('relative/path.txt')).rejects.toThrow(/absolute/);
    await expect(run(join(h.files, 'big.bin'), 1024)).rejects.toThrow(/200 MB|too large/);
  });
});

describe('retention', () => {
  it('keeps the last runs, recent runs and protected runs', async () => {
    let rt;
    ({ h, rt } = await automationHarness());
    const projectId = rt.createProject({ name: 'Q', goal: 'G' });
    const { automation } = rt.automations.create(projectId, 'w', waitDef(), { origin: 'user', via: 'editor' });
    const ids: string[] = [];
    for (let i = 0; i < 25; i++) {
      const id = `r${String(i).padStart(2, '0')}`;
      ids.push(id);
      h.store.append({ project_id: projectId, agent_id: null, type: 'automation.run_started', payload: { run_id: id, automation_id: automation.id, version: 1, trigger: 'manual', test: false, inputs: {}, by: 'user', deadline_at: '2027-01-01T00:00:00.000Z' } });
      h.store.append({ project_id: projectId, agent_id: null, type: 'automation.run_finished', payload: { run_id: id, status: 'succeeded', summary: 'ok' } });
      mkdirSync(runDir(h.dir, id), { recursive: true });
    }
    // All 25 started "now": with days = 0 only the 20 newest (by started_at, then id) and protected ones stay.
    const removed = pruneRuns({ db: h.store.db, dataDir: h.dir, automationId: automation.id, keep: 20, days: 0, now: new Date('2030-01-01T00:00:00.000Z'), protect: new Set(['r00']) });
    expect(removed.sort()).toEqual(['r01', 'r02', 'r03', 'r04']);
    expect(existsSync(runDir(h.dir, 'r00'))).toBe(true);
    expect(existsSync(runDir(h.dir, 'r01'))).toBe(false);
  });
});
