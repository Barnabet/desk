import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { getAgent, getDeskAgent, listAgents, listSources } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

const fakeModels = { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id };

describe('projects and Desk', () => {
  it('creates exactly one Desk agent with the desk model and its directories', async () => {
    h = await createHarness();
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id } });
    const desk = getDeskAgent(h.store.db, projectId)!;
    expect(desk).toMatchObject({ role: 'desk', model: FAKE_MODEL.id, status: 'idle' });
    expect(listAgents(h.store.db, projectId)).toHaveLength(1);
    expect(existsSync(desk.workspace_path!)).toBe(true);
    expect(existsSync(join(h.dir, 'projects', projectId, 'library'))).toBe(true);
  });

  it('routes user messages to Desk', async () => {
    h = await createHarness({ script: [text('On it.')] });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'Ship v1', settings: fakeModels });
    rt.sendToDesk(projectId, 'hello Desk');
    await rt.whenIdle();
    expect(h.fake.requests[0]!.messages.at(-1)).toEqual({ role: 'user', content: 'hello Desk' });
  });
});

describe('sources', () => {
  it('detects git repositories and plain folders', async () => {
    h = await createHarness();
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: fakeModels });
    const repo = join(h.files, 'repo');
    mkdirSync(repo);
    execFileSync('git', ['init', '-q', repo]);
    const folder = join(h.files, 'docs');
    mkdirSync(folder);
    await rt.addSource(projectId, repo, 'API repo');
    await rt.addSource(projectId, folder);
    expect(listSources(h.store.db, projectId).map((s) => [s.kind, s.label])).toEqual([
      ['git', 'API repo'],
      ['folder', 'docs'],
    ]);
    await expect(rt.addSource(projectId, join(h.dir, 'missing'))).rejects.toThrow(/does not exist/);
  });

  it('removes sources', async () => {
    h = await createHarness();
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: fakeModels });
    const id = await rt.addSource(projectId, h.files);
    rt.removeSource(projectId, id);
    expect(listSources(h.store.db, projectId)).toHaveLength(0);
  });
});

describe('agent messages', () => {
  it('wakes the recipient and renders an origin tag', async () => {
    h = await createHarness({ script: [text('ack')] });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: fakeModels });
    const desk = getDeskAgent(h.store.db, projectId)!;
    const thread = rt.createThread(projectId, { title: 'Research', brief: 'B', workspacePath: join(h.dir, 'w') });
    rt.sendAgentMessage(thread, desk.id, 'completed', 'All done.');
    await rt.whenIdle();
    expect(h.fake.requests[0]!.messages.at(-1)).toEqual({
      role: 'user',
      content: `[from thread "Research" (${thread}) — completed] All done.`,
    });
    expect(getAgent(h.store.db, desk.id)?.status).toBe('idle');
  });

  it('labels Desk messages to threads', async () => {
    h = await createHarness({ script: [text('ok')] });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: fakeModels });
    const desk = getDeskAgent(h.store.db, projectId)!;
    const thread = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w') });
    rt.sendAgentMessage(desk.id, thread, 'revision', 'Add tests.');
    await rt.whenIdle();
    expect(h.fake.requests[0]!.messages.at(-1)).toEqual({ role: 'user', content: '[from Desk — revision] Add tests.' });
  });
});
