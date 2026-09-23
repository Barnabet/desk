import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '../testing/harness';
import { testToolContext } from '../testing/context';
import { libraryListTool, libraryPublishTool, libraryReadTool } from '../tools/library';
import { ToolDenied } from '../tools/types';
import { listArtifacts } from './library';

let h: Harness;
afterEach(async () => h?.cleanup());

async function setup() {
  h = await createHarness();
  const rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G' });
  const ws = join(h.dir, 'ws');
  mkdirSync(ws);
  const ctx = testToolContext(ws, { projectId, agentId: 'agent1', services: rt.services });
  return { rt, projectId, ws, ctx };
}

describe('library', () => {
  it('publishes workspace files with origin and de-duplicated names', async () => {
    const { rt, projectId, ws, ctx } = await setup();
    writeFileSync(join(ws, 'report.md'), '# v1');
    const first = String(await libraryPublishTool.execute({ path: 'report.md', title: 'Report', kind: 'report', description: 'first' }, ctx));
    writeFileSync(join(ws, 'report.md'), '# v2');
    const second = String(await libraryPublishTool.execute({ path: 'report.md', title: 'Report', kind: 'report', description: 'second' }, ctx));
    expect(first).toContain('report.md');
    expect(second).toContain('report-2.md');
    expect(readFileSync(join(rt.libraryDir(projectId), 'report-2.md'), 'utf8')).toBe('# v2');
    expect(listArtifacts(h.store.db, projectId).map((a) => [a.path, a.origin, a.kind])).toEqual([
      ['report.md', 'agent:agent1', 'report'],
      ['report-2.md', 'agent:agent1', 'report'],
    ]);
  });

  it('refuses to publish files outside the workspace', async () => {
    const { ctx } = await setup();
    writeFileSync(join(h.dir, 'outside.txt'), 'x');
    await expect(
      libraryPublishTool.execute({ path: join(h.dir, 'outside.txt'), title: 'x', kind: 'file', description: '' }, ctx),
    ).rejects.toBeInstanceOf(ToolDenied);
  });

  it('reads library files without escaping the library', async () => {
    const { rt, projectId, ctx } = await setup();
    rt.addLibraryFile(projectId, { name: 'brief.txt', content: 'Customer brief', title: 'Brief' });
    expect(String(await libraryReadTool.execute({ path: 'brief.txt' }, ctx))).toContain('Customer brief');
    await expect(libraryReadTool.execute({ path: '../desk/x' }, ctx)).rejects.toBeInstanceOf(ToolDenied);
  });

  it('lists artifacts including user uploads', async () => {
    const { rt, projectId, ctx } = await setup();
    rt.addLibraryFile(projectId, { name: 'spec.pdf', content: Buffer.from([1, 2, 3]), title: 'Spec', kind: 'file', description: 'from user' });
    expect(String(await libraryListTool.execute({}, ctx))).toBe('- spec.pdf — Spec [file] (user): from user');
    expect(listArtifacts(h.store.db, projectId)[0]?.origin).toBe('user');
    expect(String(await libraryListTool.execute({}, testToolContext(h.dir, { projectId: rt.createProject({ name: 'E', goal: 'G' }), services: rt.services })))).toBe(
      'The library is empty',
    );
  });
});
