import { afterEach, describe, expect, it } from 'vitest';
import { call, error, fakeEncrypted, text, type FakeReply } from '@desk/fake-model';
import type { Reasoning } from '@desk/protocol';
import { buildToolContext } from '../agent/context';
import { threadSystemPrompt } from '../agent/prompts';
import { runAgent } from '../agent/run';
import { createHarness, FAKE_GPT, newRuntime, noSleep, seedThread, type Harness } from '../testing/harness';
import { fileTools } from '../tools/fs';
import { JobManager } from '../tools/jobs';
import { completeTool } from '../tools/thread';
import { apiOf, createModelAdapter } from './adapter';
import { SEED_MODELS } from './registry';
import { toResponsesInput } from './responses';
import type { ChatMessage } from './types';

let h: Harness | undefined;
afterEach(async () => {
  await h?.cleanup();
  h = undefined;
});

/** A reply's reasoning as the fake Responses API encrypts it (`id` as the fake numbers its items). */
const encrypted = (thinking: string, id: string, phase?: string, model = FAKE_GPT.id): Reasoning => ({
  model,
  blocks: [{ type: 'encrypted_reasoning', id, encrypted_content: fakeEncrypted(thinking), summary: [thinking] }],
  ...(phase ? { phase } : {}),
});
const thinkThenCall = (thinking: string, ...calls: ReturnType<typeof call>[]): FakeReply => ({ kind: 'reply', thinking, toolCalls: calls });

it('talks to GPT models through the Responses API and to Claude through Messages unless the registry says otherwise', () => {
  expect(SEED_MODELS.map((m) => [m.id, apiOf(m)])).toEqual([
    ['claude-opus-5-5', 'messages'],
    ['claude-fable-5-1', 'messages'],
    ['gpt-6-astra', 'responses'],
    ['gpt-6-sol', 'responses'],
  ]);
  expect(apiOf({ ...SEED_MODELS[2]!, api: 'chat' })).toBe('chat');
});

describe('toResponsesInput', () => {
  const conversation: ChatMessage[] = [
    { role: 'system', content: 'You are a test agent.' },
    { role: 'user', content: 'Look at the chart.' },
    {
      role: 'assistant',
      content: 'Viewing it.',
      tool_calls: [{ id: 'c1', type: 'function', function: { name: 'view_image', arguments: '{"paths":["a.png"]}' } }],
      reasoning: encrypted('I should look first.', 'rs_1', 'commentary'),
    },
    { role: 'tool', tool_call_id: 'c1', content: 'shown' },
    { role: 'user', content: [{ type: 'text', text: '[Images from view_image]' }, { type: 'image_url', image_url: { url: 'data:image/png;base64,AAAA' } }] },
  ];

  it('makes the system prompt the instructions, and replays the reasoning, the message with its phase and the call', () => {
    const { instructions, input } = toResponsesInput(conversation, FAKE_GPT.id);
    expect(instructions).toBe('You are a test agent.');
    expect(input).toEqual([
      { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Look at the chart.' }] },
      { type: 'reasoning', id: 'rs_1', encrypted_content: fakeEncrypted('I should look first.'), summary: [{ type: 'summary_text', text: 'I should look first.' }] },
      { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Viewing it.' }], phase: 'commentary' },
      { type: 'function_call', call_id: 'c1', name: 'view_image', arguments: '{"paths":["a.png"]}' },
      { type: 'function_call_output', call_id: 'c1', output: 'shown' },
      { type: 'message', role: 'user', content: [{ type: 'input_text', text: '[Images from view_image]' }, { type: 'input_image', image_url: 'data:image/png;base64,AAAA' }] },
    ]);
  });

  it('sends reasoning only to the model that wrote it', () => {
    const { input } = toResponsesInput(conversation, 'gpt-6-astra');
    expect(input.map((i) => i.type)).toEqual(['message', 'message', 'function_call', 'function_call_output', 'message']);
    expect(input[1]).not.toHaveProperty('phase');
  });
});

describe('the Responses adapter', () => {
  async function setup(script: FakeReply[]) {
    h = await createHarness({ script });
    h.models.upsert(FAKE_GPT);
    return createModelAdapter({ baseURL: h.fake.url, apiKey: 'test' }, h.models);
  }

  it('asks for nothing stored and the reasoning encrypted, at the chosen effort, and returns it', async () => {
    const adapter = await setup([{ kind: 'reply', thinking: 'One file.', text: 'Reading it.', toolCalls: [call('read_file', { path: 'a.md' }, 'call_1')] }]);
    const deltas: string[] = [];
    const result = await adapter.complete(
      {
        model: FAKE_GPT.id,
        messages: [{ role: 'system', content: 'Be brief.' }, { role: 'user', content: 'Read a.md' }],
        tools: [{ type: 'function', function: { name: 'read_file', description: 'Reads a file', parameters: { type: 'object', properties: { path: { type: 'string' } } } } }],
        reasoningEffort: 'low',
      },
      { onText: (d) => deltas.push(d) },
    );
    expect(h!.fake.requests[0]).toMatchObject({
      model: FAKE_GPT.id,
      instructions: 'Be brief.',
      input: [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Read a.md' }] }],
      tools: [{ type: 'function', name: 'read_file', description: 'Reads a file', parameters: { type: 'object' }, strict: false }],
      reasoning: { effort: 'low' },
      store: false,
      include: ['reasoning.encrypted_content'],
      stream: true,
    });
    expect(result).toMatchObject({
      content: 'Reading it.',
      toolCalls: [{ id: 'call_1', name: 'read_file', arguments: '{"path":"a.md"}' }],
      finishReason: 'tool_calls',
      usage: { estimated: false, completion_tokens: 10 },
    });
    expect(result.reasoning).toEqual(encrypted('One file.', result.reasoning!.blocks[0]!.type === 'encrypted_reasoning' ? result.reasoning!.blocks[0]!.id : '', 'commentary'));
    expect(deltas.join('')).toBe('Reading it.');
  });

  it('sends no reasoning settings without a level, and classifies errors', async () => {
    const adapter = await setup([text('Hi.'), error(429, 'rate_limit_exceeded', 'slow down'), error(400, 'invalid_request_error', 'Your input exceeds the context window of this model.')]);
    const req = { model: FAKE_GPT.id, messages: [{ role: 'user' as const, content: 'hi' }], tools: [] };
    expect(await adapter.complete(req)).toMatchObject({ content: 'Hi.', finishReason: 'stop', toolCalls: [] });
    expect(h!.fake.requests[0]!.reasoning).toBeUndefined();
    await expect(adapter.complete(req)).rejects.toMatchObject({ kind: 'rate_limited', status: 429 });
    await expect(adapter.complete(req)).rejects.toMatchObject({ kind: 'context_overflow', status: 400 });
  });
});

describe('an agent on the Responses API', () => {
  it('keeps its encrypted reasoning in the event log and sends it back on the next step', async () => {
    h = await createHarness({
      script: [thinkThenCall('First I write the file.', call('write_file', { path: 'out.txt', content: 'x' })), thinkThenCall('Written; I can finish.', call('complete', { summary: 'Wrote out.txt' }))],
    });
    h.models.upsert(FAKE_GPT);
    const { agentId, projectId } = await seedThread(h.store, h.dir, { model: FAKE_GPT.id });
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
    const kept = h.store.list({ agentId, types: ['assistant.message'] }).map((e) => (e.type === 'assistant.message' ? e.payload.reasoning : undefined));
    expect(kept.map((r) => r?.blocks.map((b) => (b.type === 'encrypted_reasoning' ? b.summary : [])))).toEqual([[['First I write the file.']], [['Written; I can finish.']]]);
    // The fake endpoint refuses a reasoning item it did not encrypt, as OpenAI does.
    const second = h.fake.requests[1]!.input as Array<Record<string, unknown>>;
    expect(second.map((i) => i.type)).toEqual(['message', 'reasoning', 'function_call', 'function_call_output']);
    expect(second[1]).toMatchObject({ encrypted_content: fakeEncrypted('First I write the file.') });
  });
});
