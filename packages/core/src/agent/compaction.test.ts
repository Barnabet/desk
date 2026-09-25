import { afterEach, describe, expect, it } from 'vitest';
import { call, error, text, tools, type ChatRequest } from '@desk/fake-model';
import type { EventBody, StoredEvent } from '@desk/protocol';
import { getAgent } from '../state/queries';
import { createHarness, newRuntime, noSleep, seedThread, type Harness } from '../testing/harness';
import { fileTools } from '../tools/fs';
import { JobManager } from '../tools/jobs';
import { threadCoordinationTools } from '../tools/thread';
import { chooseSplit, renderForCompaction, shouldCompact } from './compaction';
import { buildToolContext } from './context';
import { runAgent, type RunDeps } from './run';
import { buildConversation, buildTaggedConversation } from './transcript';

let seq = 0;
const ev = (body: EventBody): StoredEvent => ({ ...body, id: ++seq, project_id: 'p', agent_id: 'a', ts: 't' }) as StoredEvent;

describe('transcript with a checkpoint', () => {
  it('replaces everything up to the checkpoint and merges it into the next user message', () => {
    seq = 0;
    const events = [
      ev({ type: 'message.user', payload: { text: 'old request' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: 'old answer', tool_calls: [] } }),
      ev({ type: 'message.user', payload: { text: 'new request' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 4 } }),
      ev({ type: 'context.compacted', payload: { run_id: 'r', checkpoint: 'Goal: X', up_to: 3, trigger: 'threshold' } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: 'new answer', tool_calls: [] } }),
    ];
    expect(buildConversation(events)).toEqual([
      { role: 'user', content: '[Checkpoint — summary of the earlier conversation]\nGoal: X\n\nnew request' },
      { role: 'assistant', content: 'new answer' },
    ]);
  });
});

describe('chooseSplit', () => {
  it('keeps the tail and never orphans tool results', () => {
    seq = 0;
    const events = [
      ev({ type: 'message.user', payload: { text: 'u1' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [{ id: 'c1', name: 'x', arguments: '{}' }, { id: 'c2', name: 'x', arguments: '{}' }] } }),
      ev({ type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'x', status: 'ok', content: 'a' } }),
      ev({ type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c2', name: 'x', status: 'ok', content: 'b' } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: 'done', tool_calls: [] } }),
    ];
    const tagged = buildTaggedConversation(events);
    // keep 2 → tail would start at the second tool result; the split moves back to the assistant tool call.
    const split = chooseSplit(tagged, 2)!;
    expect(tagged.slice(split.index).map((m) => m.message.role)).toEqual(['assistant', 'tool', 'tool', 'assistant']);
    expect(split.upTo).toBe(2);
    expect(chooseSplit(tagged, 10)).toBeNull();
  });

  it('never separates the images message from the tool results it follows', () => {
    seq = 0;
    const image = { sha256: 'a'.repeat(64), media_type: 'image/png' as const, width: 1240, height: 1754, bytes: 9, name: 'page-3.png' };
    const events = [
      ev({ type: 'message.user', payload: { text: 'u1' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [{ id: 'c1', name: 'view_image', arguments: '{}' }] } }),
      ev({ type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'view_image', status: 'ok', content: 'page-3.png', images: [image] } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: 'done', tool_calls: [] } }),
    ];
    const tagged = buildTaggedConversation(events);
    expect(tagged.map((m) => m.message.role)).toEqual(['user', 'assistant', 'tool', 'user', 'assistant']);
    // keep 2 → the tail would start at the images message; the split moves back to the tool call.
    const split = chooseSplit(tagged, 2)!;
    expect(tagged.slice(split.index).map((m) => m.message.role)).toEqual(['assistant', 'tool', 'user', 'assistant']);
    // Summaries show images as references, never pixels.
    const rendered = renderForCompaction(tagged.map((m) => m.message), 100_000);
    expect(rendered).toContain('[image: page-3.png 1240×1754]');
    expect(renderForCompaction([{ role: 'user', content: [{ type: 'text', text: 'see' }, { type: 'image_url', image_url: { url: 'data:image/png;base64,AAAA' } }] }], 100_000)).toBe('USER:\nsee\n[image]');
  });

  it('triggers at 70% of the context window', () => {
    expect(shouldCompact(69_999, 100_000)).toBe(false);
    expect(shouldCompact(70_000, 100_000)).toBe(true);
  });
});

let h: Harness;
afterEach(async () => h?.cleanup());

const isCompaction = (r: ChatRequest) => !r.tools && String(r.messages.at(-1)?.content).includes('Write the checkpoint');

describe('compaction in the run loop', () => {
  const deps = (extra: Partial<RunDeps> = {}): RunDeps => {
    const rt = newRuntime(h);
    return {
      store: h.store,
      adapter: h.adapter,
      tools: [...fileTools, ...threadCoordinationTools],
      systemPrompt: () => 'system',
      maxSteps: 20,
      retry: noSleep,
      gate: () => ({ action: 'auto', delegateToDesk: false, reason: '' }),
      toolContext: (a, runId, id, signal) => buildToolContext(a, runId, id, signal, { sandboxEnabled: false, jobs: new JobManager(), services: rt.services }),
      contextWindow: () => 100_000,
      compaction: { keepMessages: 2 },
      ...extra,
    };
  };

  it('compacts after a call that crosses the threshold, then continues with a smaller context', async () => {
    h = await createHarness({
      script: (req) => {
        if (isCompaction(req)) return text('Goal: list files. State: listed once.');
        const n = h.fake.requests.filter((r) => !isCompaction(r)).length;
        if (n === 1) return tools(call('list_dir', {}));
        if (n === 2) return { kind: 'reply', toolCalls: [{ name: 'list_dir', args: {} }], usage: { prompt_tokens: 80_000, completion_tokens: 5 } };
        return text('done');
      },
    });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'message.user', payload: { text: 'list twice' } });
    const outcome = await runAgent(deps(), agentId, new AbortController().signal);
    expect(outcome.reason).toBe('no_tool_calls');
    const compacted = h.store.list({ agentId, types: ['context.compacted'] });
    expect(compacted).toHaveLength(1);
    const main = h.fake.requests.filter((r) => !isCompaction(r));
    const last = main.at(-1)!.messages;
    expect(String(last[1]!.content)).toContain('Goal: list files.');
    expect(last.length).toBeLessThan(main[1]!.messages.length + 2);
    expect(getAgent(h.store.db, agentId)?.status).toBe('idle');
  });

  it('forces compaction once on context overflow, and fails on a second overflow', async () => {
    h = await createHarness({
      script: (req) => {
        if (isCompaction(req)) return text('Checkpoint.');
        const n = h.fake.requests.filter((r) => !isCompaction(r)).length;
        if (n === 1) return tools(call('list_dir', {}));
        if (n === 2) return error(400, 'invalid_request_error', 'prompt is too long: 250000 tokens > 200000 maximum');
        return text('recovered');
      },
    });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'message.user', payload: { text: 'go' } });
    expect((await runAgent(deps(), agentId, new AbortController().signal)).reason).toBe('no_tool_calls');
    expect(h.store.list({ agentId, types: ['context.compacted'] })).toHaveLength(1);

    h.fake.setScript((req) => (isCompaction(req) ? text('Checkpoint.') : error(400, 'invalid_request_error', 'prompt is too long')));
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'message.user', payload: { text: 'again' } });
    const second = await runAgent(deps(), agentId, new AbortController().signal);
    expect(second).toMatchObject({ reason: 'error', status: 'failed' });
  });

  it('a failed compaction leaves the conversation intact and the run continues', async () => {
    h = await createHarness({
      script: (req) => {
        if (isCompaction(req)) return error(400, 'invalid_request_error', 'bad compaction');
        const n = h.fake.requests.filter((r) => !isCompaction(r)).length;
        if (n === 1) return { kind: 'reply', toolCalls: [{ name: 'list_dir', args: {} }], usage: { prompt_tokens: 90_000, completion_tokens: 5 } };
        if (n === 2) return { kind: 'reply', toolCalls: [{ name: 'list_dir', args: {} }], usage: { prompt_tokens: 90_000, completion_tokens: 5 } };
        return text('done');
      },
    });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'message.user', payload: { text: 'go' } });
    expect((await runAgent(deps(), agentId, new AbortController().signal)).reason).toBe('no_tool_calls');
    expect(h.store.list({ agentId, types: ['context.compacted'] })).toHaveLength(0);
    // Tried once, not again on every later step.
    expect(h.fake.requests.filter(isCompaction)).toHaveLength(1);
    expect(h.fake.requests.at(-1)!.messages[1]!.content).toBe('go');
  });
});
