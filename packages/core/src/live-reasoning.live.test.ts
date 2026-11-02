import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createModelAdapter, EventStore, getAgent, getDeskAgent, loadModelConfig, ModelRegistry, openDb, Runtime, SEED_MODELS } from './index';

const BRIEF = [
  'Work in small steps, one tool call per step:',
  '1. write_file primes.txt with the first 15 prime numbers, one per line.',
  '2. read_file primes.txt.',
  '3. write_file sums.txt with the sum of the even-indexed lines (1st, 3rd, 5th, …) and the sum of the others, one per line.',
  '4. read_file sums.txt and check it against your own arithmetic.',
  '5. complete with both sums.',
].join('\n');

/** One thread doing BRIEF on claude-opus-5-5 through `api`; what it cost and whether its thinking was kept. */
async function runThread(api: 'chat' | 'messages') {
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-reasoning-')));
  const { db, close } = openDb(':memory:');
  const store = new EventStore(db);
  const models = new ModelRegistry(SEED_MODELS.map((m) => (m.id === 'claude-opus-5-5' ? { ...m, api } : m)));
  const rt = new Runtime({ store, adapter: createModelAdapter(loadModelConfig(), models), models, dataDir: dir, sandboxAvailable: false, retry: { maxAttempts: 3 }, whatsUpReminder: false });
  try {
    const p = rt.createProject({ name: 'Primes', goal: 'Arithmetic check.', settings: { thread_model: 'claude-opus-5-5', thread_reasoning_effort: (process.env.DESK_LIVE_EFFORT as 'medium' | undefined) ?? 'medium' } });
    const id = rt.createThread(p, { title: 'Primes', brief: BRIEF, workspacePath: join(dir, 'primes') });
    rt.deliver(getDeskAgent(db, p)!.id, id, 'start', 'Begin your assignment.');
    await rt.whenIdle();
    const usage = store.list({ agentId: id, types: ['usage'] }).flatMap((e) => (e.type === 'usage' ? [e.payload] : []));
    const replies = store.list({ agentId: id, types: ['assistant.message'] }).flatMap((e) => (e.type === 'assistant.message' ? [e.payload] : []));
    return {
      api,
      status: getAgent(db, id)!.status,
      calls: usage.length,
      completion: usage.reduce((n, u) => n + u.completion_tokens, 0),
      prompt: usage.reduce((n, u) => n + u.prompt_tokens, 0),
      cached: usage.reduce((n, u) => n + (u.cached_tokens ?? 0), 0),
      withThinking: replies.filter((r) => r.reasoning).length,
    };
  } finally {
    await rt.shutdown();
    close();
    await rm(dir, { recursive: true, force: true });
  }
}

describe('live: reasoning carried across tool steps', () => {
  it('keeps the signed thinking on the Messages API and sends it back on every step', async () => {
    const messages = await runThread('messages');
    const chat = await runThread('chat');
    console.log(JSON.stringify({ messages, chat }));
    expect(messages.status).toBe('done');
    expect(messages.calls).toBeGreaterThanOrEqual(3);
    // Each step after the first went out with the earlier steps' thinking (the endpoint refuses a bad signature).
    expect(messages.withThinking).toBeGreaterThan(0);
    expect(chat.withThinking).toBe(0);
  }, 600_000);
});
