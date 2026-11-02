import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createModelAdapter, EventStore, getDeskAgent, loadModelConfig, ModelRegistry, openDb, Runtime } from './index';

/** Usage of each of an agent's model calls, oldest first. */
function usages(store: EventStore, agentId: string) {
  return store.list({ agentId, types: ['usage'] }).flatMap((e) => (e.type === 'usage' ? [{ prompt: e.payload.prompt_tokens, cached: e.payload.cached_tokens ?? 0 }] : []));
}

describe('live: prompt caching across turns', () => {
  it("reads Desk's earlier context from the cache on the first call of the next turn, after the project changed", async () => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-cache-')));
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    const models = new ModelRegistry();
    const real = createModelAdapter(loadModelConfig(), models);
    const sent: unknown[] = [];
    const adapter: typeof real = { ...real, complete: (req, opts) => (sent.push(JSON.parse(JSON.stringify(req))), real.complete(req, opts)) };
    const rt = new Runtime({ store, adapter, models, dataDir: dir, sandboxAvailable: false, retry: { maxAttempts: 3 }, whatsUpReminder: false });
    try {
      const p = rt.createProject({ name: 'Launch', goal: 'Plan the product launch.', settings: { desk_model: 'claude-opus-5-5', desk_reasoning_effort: 'low' } });
      const desk = getDeskAgent(db, p)!;
      rt.sendToDesk(p, 'Just reply with one short sentence and no tool calls: say hello.');
      await rt.whenIdle();
      const first = usages(store, desk.id);

      // What used to change the system prompt between turns: memory, the library, a thread.
      rt.writeMemory(p, { kind: 'decision', content: 'The launch is on Friday.' });
      rt.addLibraryFile(p, { name: 'brief.md', content: 'x', title: 'Launch brief' });
      rt.sendToDesk(p, 'Just reply with one short sentence and no tool calls: what day is the launch?');
      await rt.whenIdle();
      const second = usages(store, desk.id).slice(first.length);

      rt.writeMemory(p, { kind: 'preference', content: 'Keep answers short.' });
      rt.sendToDesk(p, 'Just reply with one short sentence and no tool calls: how should you answer?');
      await rt.whenIdle();
      const third = usages(store, desk.id).slice(first.length + second.length);
      console.log(JSON.stringify({ first, second, third }));
      if (process.env.DESK_LIVE_DUMP) (await import('node:fs')).writeFileSync(process.env.DESK_LIVE_DUMP, JSON.stringify(sent));

      // The proxy (CLIProxyAPI, cloaking as Claude Code) moves the system prompt into a system message after the first
      // user message; on the first turn that message is the last one and is marked differently, so the second turn
      // cannot reuse it. From then on, everything but the new state note and the new message comes from the cache.
      const opening = third[0]!;
      expect(opening.prompt).toBeGreaterThan(5000);
      expect(opening.cached / opening.prompt).toBeGreaterThan(0.9);
    } finally {
      await rt.shutdown();
      close();
      await rm(dir, { recursive: true, force: true });
    }
  }, 300_000);
});
