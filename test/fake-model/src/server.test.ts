import OpenAI from 'openai';
import { afterEach, describe, expect, it } from 'vitest';
import { call, error, startFakeModel, text, tools, type FakeModelServer } from '@desk/fake-model';

let fake: FakeModelServer;
afterEach(async () => fake?.close());

const client = () => new OpenAI({ baseURL: fake.url, apiKey: 'test', maxRetries: 0 });

describe('fake model server', () => {
  it('streams text and usage', async () => {
    fake = await startFakeModel([text('hello world, streaming', { usage: { prompt_tokens: 7, completion_tokens: 3 } })]);
    const stream = await client().chat.completions.create({
      model: 'fake-model',
      messages: [{ role: 'user', content: 'hi' }],
      stream: true,
      stream_options: { include_usage: true },
    });
    let content = '';
    let usage: unknown;
    for await (const chunk of stream) {
      content += chunk.choices[0]?.delta?.content ?? '';
      if (chunk.usage) usage = chunk.usage;
    }
    expect(content).toBe('hello world, streaming');
    expect(usage).toMatchObject({ prompt_tokens: 7, completion_tokens: 3 });
    expect(fake.requests).toHaveLength(1);
  });

  it('streams parallel tool calls with split arguments', async () => {
    fake = await startFakeModel([tools(call('read_file', { path: 'a.md' }, 'toolu_1'), call('list_dir', { path: 'src' }))]);
    const stream = await client().chat.completions.create({ model: 'fake-model', messages: [{ role: 'user', content: 'x' }], stream: true });
    const calls: Record<number, { id: string; name: string; args: string }> = {};
    let finish = '';
    for await (const chunk of stream) {
      const choice = chunk.choices[0];
      if (choice?.finish_reason) finish = choice.finish_reason;
      for (const tc of choice?.delta?.tool_calls ?? []) {
        const c = (calls[tc.index] ??= { id: '', name: '', args: '' });
        if (tc.id) c.id = tc.id;
        if (tc.function?.name) c.name += tc.function.name;
        if (tc.function?.arguments) c.args += tc.function.arguments;
      }
    }
    expect(finish).toBe('tool_calls');
    expect(calls[0]).toEqual({ id: 'toolu_1', name: 'read_file', args: '{"path":"a.md"}' });
    expect(calls[1]?.name).toBe('list_dir');
    expect(calls[1]?.id).toMatch(/^call_fake_/);
  });

  it('returns errors with status and type', async () => {
    fake = await startFakeModel([error(429, 'rate_limit_error', 'slow down')]);
    await expect(
      client().chat.completions.create({ model: 'fake-model', messages: [{ role: 'user', content: 'x' }] }),
    ).rejects.toMatchObject({ status: 429 });
  });

  it('answers 500 when the script is exhausted', async () => {
    fake = await startFakeModel([]);
    await expect(
      client().chat.completions.create({ model: 'fake-model', messages: [{ role: 'user', content: 'x' }] }),
    ).rejects.toMatchObject({ status: 500 });
  });

  it('tracks max in-flight requests', async () => {
    fake = await startFakeModel(() => text('ok', { delayMs: 100 }));
    const c = client();
    await Promise.all(
      [1, 2, 3].map(() => c.chat.completions.create({ model: 'fake-model', messages: [{ role: 'user', content: 'x' }] })),
    );
    expect(fake.maxInFlight).toBe(3);
  });
});
