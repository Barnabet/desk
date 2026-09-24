import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import type { StoredEvent } from '@desk/protocol';
import { DeskStream, type StreamStatus } from './stream';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

const until = async (pred: () => boolean, ms = 3000) => {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 10));
  }
};

describe('DeskStream against deskd', () => {
  it('replays, goes live, streams deltas, and sends hello', async () => {
    h = await createHarness({ script: () => text('streamed reply text') });
    const runtime = newRuntime(h);
    server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: 'v' }), store: h.store, token: 't', port: 0 });
    const p = runtime.createProject({ name: 'P', goal: 'g' });
    const events: StoredEvent[] = [];
    const deltas: string[] = [];
    const statuses: StreamStatus[] = [];
    const s = new DeskStream({
      credentials: () => ({ baseUrl: `http://127.0.0.1:${server!.port}`, token: 't' }),
      projectId: p,
      afterSeq: 0,
      hello: { client: 'desktop', notifications: true },
      onEvent: (e) => events.push(e),
      onEphemeral: (e) => deltas.push(e.payload.text),
      onStatus: (st) => statuses.push(st),
    });
    s.start();
    await until(() => statuses.includes('live'));
    expect(events.map((e) => e.type)).toEqual(['project.created', 'agent.created']);
    expect(server!.notifyingClients()).toBe(1);
    runtime.sendToDesk(p, 'hello');
    await until(() => events.some((e) => e.type === 'assistant.message'));
    expect(deltas.join('')).toBe('streamed reply text');
    expect(s.lastSeq).toBe(events.at(-1)!.id);
    s.close();
    await until(() => statuses.at(-1) === 'closed');
    await runtime.shutdown();
  });
});

/** A scriptable WebSocket stand-in for reconnect logic. */
class FakeWS {
  static instances: FakeWS[] = [];
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeWS.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }
  send(s: string) {
    this.sent.push(s);
  }
  close() {
    this.onclose?.({ code: 1000 });
  }
  emit(m: unknown) {
    this.onmessage?.({ data: JSON.stringify(m) });
  }
  drop(code = 1006) {
    this.onclose?.({ code });
  }
}

describe('DeskStream reconnects', () => {
  it('resumes from the last applied event, skips duplicates, and refreshes credentials on 4401', async () => {
    FakeWS.instances = [];
    let token = 'a';
    let refreshed = 0;
    const got: number[] = [];
    const s = new DeskStream({
      credentials: () => ({ baseUrl: 'http://127.0.0.1:1', token }),
      refresh: async () => {
        refreshed++;
        token = 'b';
        return true;
      },
      projectId: '*',
      afterSeq: 5,
      onEvent: (e) => got.push(e.id),
      WebSocketImpl: FakeWS as unknown as typeof WebSocket,
      backoff: { minMs: 1, maxMs: 4 },
      random: () => 1,
    });
    s.start();
    await until(() => FakeWS.instances[0]!.sent.length === 1);
    expect(JSON.parse(FakeWS.instances[0]!.sent[0]!)).toEqual({ subscribe: { project_id: '*', after_seq: 5 } });
    const e = (id: number) => ({ kind: 'event', event: { id, project_id: 'p', agent_id: null, ts: '', type: 'message.user', payload: { text: 'x' } } });
    FakeWS.instances[0]!.emit(e(6));
    FakeWS.instances[0]!.emit(e(6));
    FakeWS.instances[0]!.emit(e(7));
    FakeWS.instances[0]!.drop();
    await until(() => FakeWS.instances.length === 2 && FakeWS.instances[1]!.sent.length === 1);
    expect(JSON.parse(FakeWS.instances[1]!.sent[0]!)).toEqual({ subscribe: { project_id: '*', after_seq: 7 } });
    FakeWS.instances[1]!.drop(4401);
    await until(() => FakeWS.instances.length === 3);
    expect(refreshed).toBe(1);
    expect(FakeWS.instances[2]!.url).toContain('token=b');
    expect(got).toEqual([6, 7]);
    s.close();
  });
});
