import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { getPlan } from '../coordination/plan';
import { listArtifacts } from '../library/library';
import { activeMemory } from '../memory/memory';
import { getAgent, getDeskAgent, listThreads } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

const all = (req: ChatRequest) => req.messages.map((m) => (typeof m.content === 'string' ? m.content : '')).join('\n');
const called = (req: ChatRequest, name: string, pred: (args: any) => boolean = () => true) =>
  req.messages.some((m) => m.role === 'assistant' && (m.tool_calls ?? []).some((tc: any) => tc.function.name === name && pred(JSON.parse(tc.function.arguments))));
const threadId = (req: ChatRequest, title: string) => new RegExp(`thread ([0-9A-Z]{26}) "${title}"`).exec(all(req))?.[1];

/** Desk: a small state machine over its own conversation. */
function desk(req: ChatRequest): FakeReply {
  const convo = all(req);
  if (!called(req, 'spawn_thread')) {
    return tools(
      call('update_plan', { items: [{ id: 'p1', title: 'Part A', status: 'in_progress' }, { id: 'p2', title: 'Part B', status: 'in_progress' }] }),
      call('spawn_thread', { title: 'A', brief: 'Write part A to a.md and publish it.' }),
      call('spawn_thread', { title: 'B', brief: 'Write part B to b.md.' }),
    );
  }
  const a = threadId(req, 'A');
  const b = threadId(req, 'B');
  const calls = [];
  if (convo.includes('— question] Which region?') && !called(req, 'message_thread', (x) => x.kind === 'note')) {
    calls.push(call('message_thread', { thread_id: a, kind: 'note', text: 'Use EU.' }));
  }
  const bDone = (convo.match(/"B" \([0-9A-Z]{26}\) — completed\]/g) ?? []).length;
  if (bDone >= 1 && !called(req, 'message_thread', (x) => x.kind === 'revision')) {
    calls.push(call('message_thread', { thread_id: b, kind: 'revision', text: 'Add a conclusion.' }));
  }
  const aDone = /"A" \([0-9A-Z]{26}\) — completed\]/.test(convo);
  if (aDone && bDone >= 2 && !called(req, 'write_file')) {
    calls.push(call('write_file', { path: 'combined.md', content: '# Combined\nA + B' }));
  } else if (called(req, 'write_file') && !called(req, 'report')) {
    calls.push(
      call('library_publish', { path: 'combined.md', title: 'Combined', kind: 'report', description: 'A and B merged' }),
      call('memory_write', { kind: 'decision', content: 'Region is EU' }),
      call('update_plan', { items: [{ id: 'p1', title: 'Part A', status: 'done' }, { id: 'p2', title: 'Part B', status: 'done' }] }),
      call('report', { headline: 'A and B are done', progress: '2/2', results: ['combined.md'] }),
    );
  } else if (called(req, 'report')) {
    return text('All done: combined.md is in the library.');
  }
  return calls.length ? tools(...calls) : tools(call('wait_for_threads', {}));
}

const threadScripts: Record<string, FakeReply[]> = {
  A: [
    tools(call('message_desk', { kind: 'question', text: 'Which region?' }), call('wait_for_reply', {})),
    tools(call('write_file', { path: 'a.md', content: 'Part A (EU)' })),
    tools(call('library_publish', { path: 'a.md', title: 'Part A', kind: 'report', description: 'A' })),
    tools(call('complete', { summary: 'Wrote part A for EU', artifacts: ['a.md'] })),
  ],
  B: [
    tools(call('write_file', { path: 'b.md', content: 'Part B' })),
    tools(call('complete', { summary: 'Wrote part B' })),
    tools(call('write_file', { path: 'b.md', content: 'Part B\n\nConclusion.' })),
    tools(call('complete', { summary: 'Wrote part B with conclusion' })),
  ],
};

describe('end-to-end coordination', () => {
  it('plans, dispatches, answers, reviews, assembles and reports', async () => {
    h = await createHarness({
      script: (req) => {
        const system = String(req.messages[0]?.content ?? '');
        if (system.startsWith('You are Desk')) return desk(req);
        const title = /## Your assignment: (\w+)/.exec(system)![1]!;
        return threadScripts[title]!.shift() ?? text('(nothing left)');
      },
    });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'Demo', goal: 'Produce parts A and B', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    rt.sendToDesk(projectId, 'Please produce parts A and B.');
    await rt.whenIdle();

    const deskAgent = getDeskAgent(h.store.db, projectId)!;
    const threads = listThreads(h.store.db, projectId);
    const a = threads.find((t) => t.title === 'A');
    const b = threads.find((t) => t.title === 'B');
    expect(a).toMatchObject({ title: 'A', status: 'done', result_artifacts: ['a.md'], review_round: 0 });
    expect(b).toMatchObject({ title: 'B', status: 'done', result_summary: 'Wrote part B with conclusion', review_round: 1 });
    expect(getAgent(h.store.db, deskAgent.id)?.status).toBe('idle');

    const aTranscript = h.store.list({ agentId: a!.id, types: ['message.agent'] }).map((e) => (e.type === 'message.agent' ? e.payload.text : ''));
    expect(aTranscript).toContain('Use EU.');
    expect(listArtifacts(h.store.db, projectId).map((x) => x.path).sort()).toEqual(['a.md', 'combined.md']);
    expect(activeMemory(h.store.db, projectId).map((m) => m.content)).toEqual(['Region is EU']);
    expect(getPlan(h.store.db, projectId)?.items.map((i) => i.status)).toEqual(['done', 'done']);
    expect(h.store.list({ projectId, types: ['report'] })).toHaveLength(1);
    const last = h.store.list({ agentId: deskAgent.id, types: ['assistant.message'] }).at(-1);
    expect(last?.type === 'assistant.message' && last.payload.content).toBe('All done: combined.md is in the library.');
  });
});
