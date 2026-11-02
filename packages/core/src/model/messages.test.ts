import { afterEach, describe, expect, it } from 'vitest';
import { call, error, fakeSignature, text, type FakeReply } from '@desk/fake-model';
import type { Reasoning } from '@desk/protocol';
import { buildToolContext } from '../agent/context';
import { threadSystemPrompt } from '../agent/prompts';
import { runAgent } from '../agent/run';
import { createHarness, FAKE_CLAUDE, newRuntime, noSleep, seedThread, type Harness } from '../testing/harness';
import { fileTools } from '../tools/fs';
import { JobManager } from '../tools/jobs';
import { completeTool } from '../tools/thread';
import { createModelAdapter } from './adapter';
import { ModelError } from './errors';
import { toMessages } from './messages';
import { ModelRegistry } from './registry';
import type { ChatMessage } from './types';

let h: Harness | undefined;
afterEach(async () => {
  await h?.cleanup();
  h = undefined;
});

const signed = (thinking: string, model = FAKE_CLAUDE.id): Reasoning => ({ model, blocks: [{ type: 'thinking', thinking, signature: fakeSignature(thinking) }] });
/** A reply that thinks, then calls tools. */
const thinkThenCall = (thinking: string, ...calls: ReturnType<typeof call>[]): FakeReply => ({ kind: 'reply', thinking, toolCalls: calls });

describe('toMessages', () => {
  const conversation: ChatMessage[] = [
    { role: 'system', content: 'You are a test agent.' },
    { role: 'user', content: '[Desk runtime — project state at …]' },
    { role: 'user', content: 'Look at the chart.' },
    {
      role: 'assistant',
      content: 'Viewing it.',
      tool_calls: [{ id: 'c1', type: 'function', function: { name: 'view_image', arguments: '{"paths":["a.png"]}' } }],
      reasoning: signed('I should look first.'),
    },
    { role: 'tool', tool_call_id: 'c1', content: 'shown' },
    { role: 'user', content: [{ type: 'text', text: '[Images from view_image]' }, { type: 'image_url', image_url: { url: 'data:image/png;base64,AAAA' } }] },
  ];

  it('puts the system prompt apart, merges each role’s turn, tool results first, and images as base64 blocks', () => {
    const { system, messages } = toMessages(conversation, FAKE_CLAUDE.id);
    expect(system).toBe('You are a test agent.');
    expect(messages).toEqual([
      { role: 'user', content: [{ type: 'text', text: '[Desk runtime — project state at …]' }, { type: 'text', text: 'Look at the chart.' }] },
      {
        role: 'assistant',
        content: [
          { type: 'thinking', thinking: 'I should look first.', signature: fakeSignature('I should look first.') },
          { type: 'text', text: 'Viewing it.' },
          { type: 'tool_use', id: 'c1', name: 'view_image', input: { paths: ['a.png'] } },
        ],
      },
      {
        role: 'user',
        content: [
          { type: 'tool_result', tool_use_id: 'c1', content: 'shown' },
          { type: 'text', text: '[Images from view_image]' },
          { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'AAAA' } },
        ],
      },
    ]);
  });

  it('sends thinking only to the model that wrote it (a fallback model gets the reply without it)', () => {
    const { messages } = toMessages(conversation, 'claude-fable-5-1');
    expect(messages[1]!.content.map((b) => b.type)).toEqual(['text', 'tool_use']);
  });
});

describe('the Messages adapter', () => {
  async function setup(script: FakeReply[]) {
    h = await createHarness({ script });
    h.models.upsert(FAKE_CLAUDE);
    return createModelAdapter({ baseURL: h.fake.url, apiKey: 'test' }, h.models);
  }

  it('sends tools, cache breakpoints and adaptive thinking at the chosen effort, and returns the signed thinking', async () => {
    const adapter = await setup([thinkThenCall('Two files to read.', call('read_file', { path: 'a.md' }, 'toolu_1'))]);
    const deltas: string[] = [];
    const result = await adapter.complete(
      {
        model: FAKE_CLAUDE.id,
        messages: [{ role: 'system', content: 'Be brief.' }, { role: 'user', content: 'Read a.md' }],
        tools: [{ type: 'function', function: { name: 'read_file', description: 'Reads a file', parameters: { type: 'object', properties: { path: { type: 'string' } } } } }],
        reasoningEffort: 'low',
      },
      { onText: (d) => deltas.push(d) },
    );
    const body = h!.fake.requests[0]!;
    expect(body).toMatchObject({
      model: FAKE_CLAUDE.id,
      max_tokens: FAKE_CLAUDE.max_output_tokens,
      stream: true,
      system: [{ type: 'text', text: 'Be brief.', cache_control: { type: 'ephemeral' } }],
      tools: [{ name: 'read_file', description: 'Reads a file', input_schema: { type: 'object' } }],
      thinking: { type: 'adaptive' },
      output_config: { effort: 'low' },
      messages: [{ role: 'user', content: [{ type: 'text', text: 'Read a.md', cache_control: { type: 'ephemeral' } }] }],
    });
    expect(result).toMatchObject({
      content: null,
      toolCalls: [{ id: 'toolu_1', name: 'read_file', arguments: '{"path":"a.md"}' }],
      finishReason: 'tool_calls',
      reasoning: signed('Two files to read.'),
      usage: { estimated: false, completion_tokens: 10 },
    });
    expect(deltas).toEqual([]);
  });

  it('streams text, turns thinking off for none, and sends no thinking settings without a level', async () => {
    const adapter = await setup([text('Hello there, friend.'), text('Hi.')]);
    h!.models.upsert({ ...FAKE_CLAUDE, reasoning_efforts: ['none', 'low'] });
    const deltas: string[] = [];
    const r = await adapter.complete({ model: FAKE_CLAUDE.id, messages: [{ role: 'user', content: 'hi' }], tools: [], reasoningEffort: 'none' }, { onText: (d) => deltas.push(d) });
    expect(r).toMatchObject({ content: 'Hello there, friend.', finishReason: 'stop', toolCalls: [] });
    expect(r.reasoning).toBeUndefined();
    expect(deltas.join('')).toBe('Hello there, friend.');
    expect(h!.fake.requests[0]).toMatchObject({ thinking: { type: 'disabled' } });
    expect(h!.fake.requests[0]!.output_config).toBeUndefined();
    await adapter.complete({ model: FAKE_CLAUDE.id, messages: [{ role: 'user', content: 'hi' }], tools: [] });
    expect(h!.fake.requests[1]!.thinking).toBeUndefined();
  });

  it('classifies errors like Chat Completions does: rate limits, overflow, an unreachable proxy', async () => {
    const adapter = await setup([error(429, 'rate_limit_error', 'slow down'), error(400, 'invalid_request_error', 'prompt is too long: 250000 tokens')]);
    const req = { model: FAKE_CLAUDE.id, messages: [{ role: 'user' as const, content: 'hi' }], tools: [] };
    await expect(adapter.complete(req)).rejects.toMatchObject({ kind: 'rate_limited', status: 429 });
    await expect(adapter.complete(req)).rejects.toMatchObject({ kind: 'context_overflow', status: 400 });
    // A port nothing listens on: the fake model's, once closed.
    const url = h!.fake.url;
    await h!.fake.close();
    const down = createModelAdapter({ baseURL: url, apiKey: 'x' }, new ModelRegistry([FAKE_CLAUDE]));
    await expect(down.complete(req)).rejects.toBeInstanceOf(ModelError);
    await expect(down.complete(req)).rejects.toMatchObject({ kind: 'proxy_down' });
  });

  it('reports a stop as aborted', async () => {
    const adapter = await setup([{ kind: 'hang' }]);
    const ctl = new AbortController();
    const pending = adapter.complete({ model: FAKE_CLAUDE.id, messages: [{ role: 'user', content: 'hi' }], tools: [] }, { signal: ctl.signal });
    setTimeout(() => ctl.abort(), 20);
    await expect(pending).rejects.toMatchObject({ kind: 'aborted' });
  });
});

describe('an agent on the Messages API', () => {
  it('keeps its signed thinking in the event log and sends it back on the next step', async () => {
    h = await createHarness({
      script: [
        thinkThenCall('First I write the file.', call('write_file', { path: 'out.txt', content: 'x' })),
        { kind: 'reply', thinking: 'The file is written; I can finish.', toolCalls: [call('complete', { summary: 'Wrote out.txt' })] },
      ],
    });
    h.models.upsert(FAKE_CLAUDE);
    const { agentId, projectId } = await seedThread(h.store, h.dir, { model: FAKE_CLAUDE.id });
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'message.user', payload: { text: 'go' } });
    const jobs = new JobManager();
    const outcome = await runAgent(
      {
        store: h.store,
        adapter: h.adapter,
        tools: [...fileTools, completeTool],
        systemPrompt: (agent, project) => threadSystemPrompt({ db: h!.store.db, agent, project, libraryDir: h!.dir }),
        maxSteps: 5,
        retry: noSleep,
        gate: () => ({ action: 'auto', delegateToDesk: false, reason: 'test' }),
        toolContext: (a, runId, id, signal) => buildToolContext(a, runId, id, signal, { sandboxEnabled: false, jobs, services: newRuntime(h!).services }),
      },
      agentId,
      new AbortController().signal,
    );
    expect(outcome.reason).toBe('yielded');
    const replies = h.store.list({ agentId, types: ['assistant.message'] });
    expect(replies.map((e) => (e.type === 'assistant.message' ? e.payload.reasoning : undefined))).toEqual([
      signed('First I write the file.'),
      signed('The file is written; I can finish.'),
    ]);
    // The fake endpoint refuses a thinking block whose signature does not match, as Anthropic does.
    const second = h.fake.requests[1]!;
    const assistant = second.messages.find((m) => m.role === 'assistant')!;
    expect(assistant.content[0]).toEqual({ type: 'thinking', thinking: 'First I write the file.', signature: fakeSignature('First I write the file.') });
    expect(second.messages.at(-1)!.content[0]).toMatchObject({ type: 'tool_result' });
  });
});
