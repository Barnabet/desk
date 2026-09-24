import { afterEach, describe, expect, it } from 'vitest';
import WebSocket from 'ws';
import { text } from '@desk/fake-model';
import type { StreamServerMessage } from '@desk/protocol';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '@desk/core/testing';
import { createApp } from './app';
import { startServer, type RunningServer } from './server';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

const TOKEN = 'stream-token';

async function setup() {
  h = await createHarness({ script: () => text('streamed reply text') });
  const runtime = newRuntime(h);
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: TOKEN, version: 't' }), store: h.store, token: TOKEN, port: 0 });
  return { runtime };
}

type Client = { ws: WebSocket; messages: StreamServerMessage[]; waitFor: (pred: (m: StreamServerMessage) => boolean) => Promise<StreamServerMessage> };
function connect(token = TOKEN): Promise<Client> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${server!.port}/v1/stream?token=${token}`);
    const messages: StreamServerMessage[] = [];
    const waiters: Array<{ pred: (m: StreamServerMessage) => boolean; resolve: (m: StreamServerMessage) => void }> = [];
    ws.on('message', (data) => {
      const m = JSON.parse(String(data)) as StreamServerMessage;
      messages.push(m);
      for (const w of [...waiters]) if (w.pred(m)) (waiters.splice(waiters.indexOf(w), 1), w.resolve(m));
    });
    ws.on('open', () =>
      resolve({
        ws,
        messages,
        waitFor: (pred) => {
          const hit = messages.find(pred);
          return hit ? Promise.resolve(hit) : new Promise((r) => waiters.push({ pred, resolve: r }));
        },
      }),
    );
    ws.on('error', reject);
  });
}
const subscribe = (c: Client, project_id: string, after_seq: number) => c.ws.send(JSON.stringify({ subscribe: { project_id, after_seq } }));
const eventIds = (c: Client) => c.messages.flatMap((m) => (m.kind === 'event' ? [m.event.id] : []));

describe('event stream', () => {
  it('rejects a bad token', async () => {
    await setup();
    const c = await connect('wrong');
    const code = await new Promise<number>((r) => c.ws.on('close', (code) => r(code)));
    expect(code).toBe(4401);
  });

  it('replays after the cursor, signals ready, then streams live events and deltas', async () => {
    const { runtime } = await setup();
    const projectId = runtime.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id } });
    const c = await connect();
    subscribe(c, projectId, 0);
    const ready = await c.waitFor((m) => m.kind === 'ready');
    expect(ready.kind === 'ready' && ready.seq).toBeGreaterThan(0);
    expect(c.messages.filter((m) => m.kind === 'event').map((m) => m.kind === 'event' && m.event.type)).toEqual(['project.created', 'agent.created']);
    runtime.sendToDesk(projectId, 'hello');
    await c.waitFor((m) => m.kind === 'event' && m.event.type === 'run.finished');
    const deltas = c.messages.flatMap((m) => (m.kind === 'ephemeral' ? [m.event.payload.text] : [])).join('');
    expect(deltas).toBe('streamed reply text');
    const ids = eventIds(c);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    expect(new Set(ids).size).toBe(ids.length);
    c.ws.close();
  });

  it('filters by project and supports "*"', async () => {
    const { runtime } = await setup();
    const a = runtime.createProject({ name: 'A', goal: 'G' });
    const b = runtime.createProject({ name: 'B', goal: 'G' });
    const onlyA = await connect();
    subscribe(onlyA, a, 0);
    await onlyA.waitFor((m) => m.kind === 'ready');
    const all = await connect();
    subscribe(all, '*', 0);
    await all.waitFor((m) => m.kind === 'ready');
    expect(onlyA.messages.every((m) => m.kind !== 'event' || m.event.project_id === a)).toBe(true);
    expect(new Set(all.messages.flatMap((m) => (m.kind === 'event' ? [m.event.project_id] : [])))).toEqual(new Set([a, b]));
    onlyA.ws.close();
    all.ws.close();
  });

  it('resumes from the last seen seq with exactly the missed events', async () => {
    const { runtime } = await setup();
    const projectId = runtime.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id } });
    const first = await connect();
    subscribe(first, projectId, 0);
    const ready = await first.waitFor((m) => m.kind === 'ready');
    const lastSeen = ready.kind === 'ready' ? ready.seq : 0;
    first.ws.close();
    runtime.sendToDesk(projectId, 'while you were away');
    await runtime.whenIdle();
    const missed = h.store.list({ projectId, after: lastSeen }).map((e) => e.id);
    const second = await connect();
    subscribe(second, projectId, lastSeen);
    await second.waitFor((m) => m.kind === 'ready');
    expect(eventIds(second)).toEqual(missed);
    second.ws.close();
  });

  it('reports invalid subscribe messages', async () => {
    await setup();
    const c = await connect();
    c.ws.send('{"nope":1}');
    const err = await c.waitFor((m) => m.kind === 'error');
    expect(err.kind === 'error' && err.message).toMatch(/subscribe/);
    c.ws.close();
  });

  it('counts desktop clients that say hello with notifications', async () => {
    await setup();
    const c = await connect();
    c.ws.send(JSON.stringify({ hello: { client: 'desktop', notifications: true } }));
    await new Promise((r) => setTimeout(r, 50));
    expect(server!.notifyingClients()).toBe(1);
    c.ws.close();
    await new Promise((r) => setTimeout(r, 50));
    expect(server!.notifyingClients()).toBe(0);
  });
});
