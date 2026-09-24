import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it } from 'vitest';
import { call, error, hang, startFakeModel, text, tools, type FakeModelServer } from '@desk/fake-model';
import { createModelAdapter } from './adapter';
import { ModelError } from './errors';
import { ModelRegistry } from './registry';

const registry = new ModelRegistry([
  { id: 'fake-model', family: 'claude', context_window: 100_000, max_output_tokens: 4096, reasoning_efforts: [], default_reasoning_effort: null, concurrency: 4 },
]);
const req = { model: 'fake-model', messages: [{ role: 'user' as const, content: 'hi' }], tools: [] };

let fake: FakeModelServer;
afterEach(async () => fake?.close());

describe('model adapter', () => {
  it('streams text through onText and returns usage', async () => {
    fake = await startFakeModel([text('hello there friend', { usage: { prompt_tokens: 12, completion_tokens: 4 } })]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const deltas: string[] = [];
    const r = await adapter.complete(req, { onText: (d) => deltas.push(d) });
    expect(r.content).toBe('hello there friend');
    expect(deltas.join('')).toBe('hello there friend');
    expect(deltas.length).toBeGreaterThan(1);
    expect(r.toolCalls).toEqual([]);
    expect(r.finishReason).toBe('stop');
    expect(r.usage).toEqual({ prompt_tokens: 12, completion_tokens: 4, estimated: false });
    expect(fake.requests[0]).toMatchObject({ stream: true, stream_options: { include_usage: true } });
    expect(fake.requests[0]?.tools).toBeUndefined();
  });

  it('sends reasoning_effort only when the registry lists that level for the model', async () => {
    fake = await startFakeModel(() => text('ok'));
    const r = new ModelRegistry([
      { id: 'fake-model', family: 'claude', context_window: 100_000, max_output_tokens: 4096, reasoning_efforts: [], default_reasoning_effort: null, concurrency: 4 },
      { id: 'thinker', family: 'gpt', context_window: 100_000, max_output_tokens: 4096, reasoning_efforts: ['low', 'xhigh'], default_reasoning_effort: null, concurrency: 4 },
    ]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, r);
    await adapter.complete({ ...req, model: 'thinker', reasoningEffort: 'xhigh' });
    await adapter.complete({ ...req, model: 'thinker', reasoningEffort: 'max' });
    await adapter.complete({ ...req, reasoningEffort: 'low' });
    expect(fake.requests.map((q) => (q as { reasoning_effort?: string }).reasoning_effort ?? null)).toEqual(['xhigh', null, null]);
  });

  it('assembles parallel streamed tool calls in index order', async () => {
    fake = await startFakeModel([tools(call('read_file', { path: 'a.md' }, 'toolu_A'), call('bash', { command: 'ls' }))]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const spec = { type: 'function' as const, function: { name: 'read_file', description: 'r', parameters: { type: 'object' } } };
    const r = await adapter.complete({ ...req, tools: [spec] });
    expect(r.content).toBeNull();
    expect(r.finishReason).toBe('tool_calls');
    expect(r.toolCalls).toEqual([
      { id: 'toolu_A', name: 'read_file', arguments: '{"path":"a.md"}' },
      { id: expect.stringMatching(/^call_fake_/), name: 'bash', arguments: '{"command":"ls"}' },
    ]);
    expect(fake.requests[0]?.tools).toHaveLength(1);
  });

  it('estimates usage when the server omits it', async () => {
    fake = await startFakeModel([text('abc', { usage: 'omit' })]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const r = await adapter.complete(req);
    expect(r.usage.estimated).toBe(true);
    expect(r.usage.prompt_tokens).toBeGreaterThan(0);
  });

  it.each([
    [error(429, 'rate_limit_error', 'slow'), 'rate_limited'],
    [error(503, 'overloaded', 'busy'), 'transient'],
    [error(400, 'invalid_request_error', 'prompt is too long: 250000 tokens > 200000 maximum'), 'context_overflow'],
    [error(401, 'authentication_error', 'bad key'), 'fatal'],
  ] as const)('classifies %j as %s', async (reply, kind) => {
    fake = await startFakeModel([reply]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const err = await adapter.complete(req).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ModelError);
    expect((err as ModelError).kind).toBe(kind);
  });

  it('classifies a refused connection as proxy_down', async () => {
    const probe = createServer();
    await new Promise<void>((r) => probe.listen(0, '127.0.0.1', r));
    const { port } = probe.address() as AddressInfo;
    await new Promise<void>((r) => probe.close(() => r()));
    const adapter = createModelAdapter({ baseURL: `http://127.0.0.1:${port}/v1`, apiKey: 't' }, registry);
    const err = await adapter.complete(req).catch((e: unknown) => e);
    expect((err as ModelError).kind).toBe('proxy_down');
  });

  it('classifies an aborted request as aborted', async () => {
    fake = await startFakeModel([hang()]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 50);
    const err = await adapter.complete(req, { signal: controller.signal }).catch((e: unknown) => e);
    expect((err as ModelError).kind).toBe('aborted');
  });
});
