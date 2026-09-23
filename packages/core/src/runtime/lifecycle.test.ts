import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, error, text, tools, type FakeReply, type Script } from '@desk/fake-model';
import { getAgent, getDeskAgent, listApprovals } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

/** Threads use the fake model; Desk keeps the seed model id, so requests can be routed by model. */
function routed(threadReplies: FakeReply[], deskReplies: FakeReply[] = []): Script {
  return (req) => (req.model === FAKE_MODEL.id ? (threadReplies.shift() ?? text('(thread idle)')) : (deskReplies.shift() ?? text('noted')));
}
const deskRequests = () => h.fake.requests.filter((r) => r.model !== FAKE_MODEL.id);
const lastDeskInput = () => deskRequests().at(-1)?.messages.at(-1)?.content as string;

async function setup(script: Script) {
  h = await createHarness({ script });
  const rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { thread_model: FAKE_MODEL.id } });
  const desk = getDeskAgent(h.store.db, projectId)!;
  const threadId = rt.createThread(projectId, { title: 'Research', brief: 'Find facts', workspacePath: join(h.dir, 'ws') });
  return { rt, projectId, desk, threadId };
}

describe('thread lifecycle notifications', () => {
  it('stores the result and tells Desk when a thread completes', async () => {
    const { rt, desk, threadId } = await setup(routed([tools(call('complete', { summary: 'Found 3 facts', artifacts: [] }))]));
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    expect(getAgent(h.store.db, threadId)).toMatchObject({ status: 'done', result_summary: 'Found 3 facts', result_artifacts: [] });
    expect(lastDeskInput()).toBe(`[from thread "Research" (${threadId}) — completed] Summary: Found 3 facts`);
    expect(getAgent(h.store.db, desk.id)?.status).toBe('idle');
  });

  it('rejects completion artifacts that are not in the library', async () => {
    const { rt, threadId } = await setup(
      routed([tools(call('complete', { summary: 'done', artifacts: ['ghost.md'] })), tools(call('complete', { summary: 'done' }))]),
    );
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    const firstResult = h.store.list({ agentId: threadId, types: ['tool.result'] })[0];
    expect(firstResult?.type === 'tool.result' && firstResult.payload).toMatchObject({ status: 'error', content: expect.stringContaining('ghost.md') });
    expect(getAgent(h.store.db, threadId)?.status).toBe('done');
  });

  it('tells Desk when a thread fails', async () => {
    const { rt, threadId } = await setup(routed([error(401, 'authentication_error', 'bad key')]));
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    expect(lastDeskInput()).toMatch(/— failed\] .*bad key/);
  });

  it('tells Desk when the user stops a thread, but not when Desk does', async () => {
    const { rt, desk, threadId } = await setup(routed([tools(call('wait_for_reply', {}))]));
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    rt.stop(threadId);
    await rt.whenIdle();
    expect(lastDeskInput()).toContain('— cancelled]');
    const other = rt.createThread(getAgent(h.store.db, threadId)!.project_id, { title: 'Other', brief: 'B', workspacePath: join(h.dir, 'ws2') });
    const before = deskRequests().length;
    rt.stopAgent(other, { by: desk.id });
    await rt.whenIdle();
    expect(deskRequests().length).toBe(before);
  });

  it('routes a thread question to Desk and the reply back to the waiting thread', async () => {
    const { rt, desk, threadId } = await setup(
      routed([tools(call('message_desk', { kind: 'question', text: 'Which region?' }), call('wait_for_reply', {})), text('Using EU.')]),
    );
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    expect(getAgent(h.store.db, threadId)?.status).toBe('waiting');
    expect(lastDeskInput()).toBe(`[from thread "Research" (${threadId}) — question] Which region?`);
    rt.sendAgentMessage(desk.id, threadId, 'note', 'EU only.');
    await rt.whenIdle();
    const threadReqs = h.fake.requests.filter((r) => r.model === FAKE_MODEL.id);
    expect(threadReqs.at(-1)!.messages.at(-1)).toEqual({ role: 'user', content: '[from Desk — note] EU only.' });
  });

  it('tells Desk about approvals a thread is waiting on', async () => {
    const { rt, projectId, threadId } = await setup(routed([tools(call('bash', { command: 'sudo ls' }))]));
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    const [ap] = listApprovals(h.store.db, projectId, 'pending');
    expect(lastDeskInput()).toContain(`— approval] Approval ${ap!.id} needed for bash`);
    expect(lastDeskInput()).toContain('Waiting for the user to decide');
  });

  it('forwards a thread turn that ends without completing', async () => {
    const { rt, threadId } = await setup(routed([text('I need the API key location before continuing.')]));
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    expect(lastDeskInput()).toContain('— update] Ended its turn without completing: I need the API key location');
  });

  it('reports stalled threads once per stall', async () => {
    const { rt, threadId } = await setup(routed([tools(call('wait_for_reply', {}))]));
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    const later = Date.now() + 16 * 60_000;
    expect(rt.checkStalls(later)).toEqual([threadId]);
    await rt.whenIdle();
    expect(lastDeskInput()).toContain('— stalled]');
    expect(rt.checkStalls(later + 60_000)).toEqual([]);
  });
});
