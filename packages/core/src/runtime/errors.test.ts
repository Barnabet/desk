import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { hang, text } from '@desk/fake-model';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import { getAgent, getProject, listProjects } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

describe('typed runtime errors', () => {
  it('uses NotFound, Conflict and Validation errors', async () => {
    h = await createHarness({ script: [text('ok')] });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    expect(() => rt.sendToDesk('nope', 'x')).toThrow(NotFoundError);
    expect(() => rt.sendMessage('nope', 'x')).toThrow(NotFoundError);
    await expect(rt.resolveApproval('nope', 'approved')).rejects.toBeInstanceOf(NotFoundError);
    await expect(rt.addSource(projectId, join(h.dir, 'missing'))).rejects.toBeInstanceOf(ValidationError);
    expect(() => rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: 'nope' })).toThrow(ValidationError);
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: FAKE_MODEL.id, parentId: null });
    await expect(rt.archiveThread(t)).rejects.toBeInstanceOf(ConflictError);
  });
});

describe('archiveProject', () => {
  it('stops every agent and hides the project', async () => {
    h = await createHarness({ script: () => hang() });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { thread_model: FAKE_MODEL.id } });
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w') });
    rt.sendMessage(t, 'go');
    await new Promise((r) => setTimeout(r, 50));
    rt.archiveProject(projectId);
    await rt.whenIdle();
    expect(getAgent(h.store.db, t)?.status).toBe('cancelled');
    expect(getProject(h.store.db, projectId)?.archived_at).toBeTruthy();
    expect(listProjects(h.store.db).map((p) => p.id)).not.toContain(projectId);
    expect(listProjects(h.store.db, { includeArchived: true }).map((p) => p.id)).toContain(projectId);
    expect(() => rt.sendToDesk(projectId, 'hi')).toThrow(ConflictError);
  });
});
