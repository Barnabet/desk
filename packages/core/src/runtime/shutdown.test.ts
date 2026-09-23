import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { hang, text } from '@desk/fake-model';
import { getAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

const statuses = (agentId: string) =>
  h.store.list({ agentId, types: ['agent.status_changed'] }).map((e) => (e.type === 'agent.status_changed' ? e.payload.status : ''));

describe('graceful shutdown and recovery', () => {
  it('leaves an interrupted agent queued and resumes it on the next start', async () => {
    h = await createHarness({ script: [hang()] });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: FAKE_MODEL.id, parentId: null });
    rt.sendMessage(t, 'go');
    await new Promise((r) => setTimeout(r, 50));
    await rt.shutdown();
    const fin = h.store.list({ agentId: t, types: ['run.finished'] }).at(-1);
    expect(fin?.type === 'run.finished' && fin.payload).toMatchObject({ reason: 'error', detail: 'daemon_shutdown' });
    expect(getAgent(h.store.db, t)?.status).toBe('queued');
    expect(statuses(t)).not.toContain('cancelled');

    h.fake.setScript([text('resumed and done')]);
    const next = newRuntime(h);
    expect(next.recover()).toEqual([t]);
    await next.whenIdle();
    expect(getAgent(h.store.db, t)?.status).toBe('idle');
    expect(h.fake.requests.at(-1)!.messages.filter((m) => m.role === 'user')).toEqual([{ role: 'user', content: 'go' }]);
  });

  it('does not schedule while shutting down; recovery picks up queued messages', async () => {
    h = await createHarness({ script: [text('hi')] });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: FAKE_MODEL.id, parentId: null });
    await rt.shutdown();
    rt.sendMessage(t, 'late message');
    await rt.whenIdle();
    expect(h.fake.requests).toHaveLength(0);
    const next = newRuntime(h);
    expect(next.recover()).toEqual([t]);
    await next.whenIdle();
    expect(h.fake.requests).toHaveLength(1);
  });

  it('kills background jobs', async () => {
    h = await createHarness();
    const rt = newRuntime(h);
    const id = rt.jobs.start('a', { command: 'sleep 30', cwd: h.dir, env: process.env, sandbox: { enabled: false, writable: [] } });
    await rt.shutdown();
    expect(rt.jobs.peek('a', id).status).toBe('killed');
  });
});
