import { afterEach, describe, expect, it } from 'vitest';
import { getDeskAgent } from '../state/queries';
import { createHarness, newRuntime, type Harness } from '../testing/harness';
import { testToolContext } from '../testing/context';
import { memorySearchTool, memoryWriteTool } from '../tools/memory';
import { activeMemory, memoryDigest, searchMemory } from './memory';

let h: Harness;
afterEach(async () => h?.cleanup());

async function project() {
  h = await createHarness();
  const rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G' });
  return { rt, projectId };
}

describe('memory', () => {
  it('supersedes entries, keeping one active and linking both ways', async () => {
    const { rt, projectId } = await project();
    const a = rt.writeMemory(projectId, { kind: 'decision', content: 'Release ships on Thursday' });
    const b = rt.writeMemory(projectId, { kind: 'decision', content: 'Release moved to Friday', supersedes: a });
    const active = activeMemory(h.store.db, projectId);
    expect(active.map((m) => m.id)).toEqual([b]);
    expect(active[0]).toMatchObject({ supersedes: a, source: 'user' });
    expect(searchMemory(h.store.db, projectId, 'release').map((m) => m.id)).toEqual([b]);
    expect(() => rt.writeMemory(projectId, { kind: 'fact', content: 'x', supersedes: a })).toThrow(/not active/);
  });

  it('deletes entries from search', async () => {
    const { rt, projectId } = await project();
    const id = rt.writeMemory(projectId, { kind: 'fact', content: 'Billing owner is Dana' });
    rt.deleteMemory(projectId, id);
    expect(searchMemory(h.store.db, projectId, 'billing')).toEqual([]);
    expect(activeMemory(h.store.db, projectId)).toEqual([]);
  });

  it('searches with ranking and tolerates punctuation', async () => {
    const { rt, projectId } = await project();
    rt.writeMemory(projectId, { kind: 'fact', content: 'The export feature was dropped because of GDPR concerns' });
    const hit = rt.writeMemory(projectId, { kind: 'decision', content: 'Release: Friday release window, release notes by Dana' });
    expect(searchMemory(h.store.db, projectId, 'release: friday?')[0]?.id).toBe(hit);
    expect(searchMemory(h.store.db, projectId, 'exporting')).toHaveLength(1);
    expect(searchMemory(h.store.db, projectId, '???')).toEqual([]);
  });

  it('isolates projects', async () => {
    const { rt, projectId } = await project();
    const other = rt.createProject({ name: 'Q', goal: 'G' });
    rt.writeMemory(other, { kind: 'fact', content: 'secret launch codename Falcon' });
    expect(searchMemory(h.store.db, projectId, 'falcon')).toEqual([]);
  });

  it('builds a digest with preferences and decisions first, capped', async () => {
    const { rt, projectId } = await project();
    for (let i = 0; i < 50; i++) rt.writeMemory(projectId, { kind: 'note', content: `note number ${i} ${'x'.repeat(100)}` });
    rt.writeMemory(projectId, { kind: 'preference', content: 'Check in only on blockers' });
    rt.writeMemory(projectId, { kind: 'decision', content: 'Use Postgres' });
    const digest = memoryDigest(h.store.db, projectId, 2000);
    expect(digest.indexOf('Check in only on blockers')).toBeLessThan(digest.indexOf('note number'));
    expect(digest.indexOf('Use Postgres')).toBeLessThan(digest.indexOf('note number'));
    expect(digest).toContain('note number 49');
    expect(digest).toMatch(/more entries — use memory_search/);
    expect(digest.length).toBeLessThanOrEqual(2100);
    expect(memoryDigest(h.store.db, rt.createProject({ name: 'E', goal: 'G' }))).toBe('');
  });
});

describe('memory tools', () => {
  it('write attributes the agent and search formats results', async () => {
    const { rt, projectId } = await project();
    const desk = getDeskAgent(h.store.db, projectId)!;
    const ctx = testToolContext(h.dir, { projectId, agentId: desk.id, services: rt.services });
    const out = String(await memoryWriteTool.execute({ kind: 'contact', content: 'Ask Priya before touching billing' }, ctx));
    const id = /mem_\w+|\b[0-9A-Z]{26}\b/.exec(out)![0];
    expect(activeMemory(h.store.db, projectId)[0]).toMatchObject({ id, source: `agent:${desk.id}`, kind: 'contact' });
    const found = String(await memorySearchTool.execute({ query: 'billing' }, ctx));
    expect(found).toContain('Ask Priya before touching billing');
    expect(found).toContain(`[contact] (${id})`);
    expect(String(await memorySearchTool.execute({ query: 'zebra' }, ctx))).toBe('No matching memory');
  });
});
