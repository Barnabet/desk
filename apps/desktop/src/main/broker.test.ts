import { afterEach, describe, expect, it } from 'vitest';
import { DeskClient, type StreamOptions } from '@desk/client';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import { WAKES_PAUSED, type StoredEvent } from '@desk/protocol';
import { text } from '@desk/fake-model';
import type { GlobalState } from '../shared/state';
import { Broker, type BrokerDeps } from './broker';

let h: Harness;
let server: RunningServer | undefined;
let broker: Broker | undefined;
afterEach(async () => {
  broker?.stop();
  broker = undefined;
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

const until = async (pred: () => boolean, ms = 3000) => {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 5));
  }
};

async function setup(extra: Partial<BrokerDeps> = {}) {
  h = await createHarness({ script: () => text('Hello from Desk') });
  const runtime = newRuntime(h);
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0' }), store: h.store, token: 't', port: 0 });
  const creds = { baseUrl: `http://127.0.0.1:${server.port}`, token: 't' };
  const sent: Array<[number, string, unknown]> = [];
  const globals: GlobalState[] = [];
  let streamOpts: StreamOptions | undefined;
  const deps: BrokerDeps = {
    connect: () => new DeskClient(creds),
    credentials: () => creds,
    send: (id, ch, p) => void sent.push([id, ch, p]),
    broadcast: (ch, p) => void (ch === 'desk:global' && globals.push(p as GlobalState)),
    hello: () => ({ client: 'desktop', notifications: true }),
    streamFactory: (o) => ((streamOpts = o), { start: () => {}, close: () => {} }),
    debounceMs: 5,
    pollMs: 5,
    ...extra,
  };
  broker = new Broker(deps);
  return { runtime, sent, globals, deps, stream: () => streamOpts!, store: h.store };
}

describe('Broker', () => {
  it('loads the global snapshot, subscribes from its seq, and goes live on ready', async () => {
    const { runtime, globals, stream } = await setup();
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    await broker!.start();
    const s = broker!.snapshot();
    expect(s.overview.map((o) => o.project.name)).toEqual(['Launch']);
    expect(stream().projectId).toBe('*');
    expect(stream().afterSeq).toBeGreaterThan(0);
    expect(stream().hello).toEqual({ client: 'desktop', notifications: true });
    stream().onReady?.(stream().afterSeq);
    expect(broker!.snapshot().connection.status).toBe('live');
    expect(globals.at(-1)!.connection.status).toBe('live');
    expect(p).toBeTruthy();
  });

  it('refetches attention after relevant events and reports new items once', async () => {
    const added: string[][] = [];
    const { runtime, stream, store } = await setup({ onAttentionAdded: (items) => void added.push(items.map((i) => i.kind)) });
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    await broker!.start();
    const desk = broker!.snapshot().overview[0]!;
    expect(desk).toBeTruthy();
    const [q] = store.append({ project_id: p, agent_id: null, type: 'question.asked', payload: { question: 'Which first?' } });
    stream().onEvent(q as StoredEvent);
    await until(() => broker!.snapshot().attention.length === 1);
    expect(added).toEqual([['question']]);
    stream().onEvent({ ...(q as StoredEvent), id: q!.id + 100, type: 'usage', payload: { run_id: 'r', model: 'm', prompt_tokens: 1, completion_tokens: 1, estimated: false } } as StoredEvent);
    await new Promise((r) => setTimeout(r, 30));
    expect(added).toHaveLength(1);
  });

  it('refetches attention when a pause notice is the only new event', async () => {
    const added: string[][] = [];
    const { runtime, stream, store } = await setup({ onAttentionAdded: (items) => void added.push(items.map((i) => i.kind)) });
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    await broker!.start();
    const [notice] = store.append({
      project_id: p,
      agent_id: null,
      type: 'system.notice',
      payload: { level: 'warning', code: WAKES_PAUSED, message: 'Agents woke each other 60 times in the last hour, so automatic wakes are paused.' },
    });
    stream().onEvent(notice as StoredEvent);
    await until(() => broker!.snapshot().attention.length === 1);
    expect(added).toEqual([['paused']]);
  });

  it('backfills a watched project, then forwards live events once, plus deltas', async () => {
    const { runtime, sent, stream, store } = await setup();
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    const other = runtime.createProject({ name: 'Other', goal: 'g' });
    await broker!.start();
    await broker!.watch(9, p, 0);
    const backfilled = sent.filter(([id, ch]) => id === 9 && ch === 'desk:events').flatMap(([, , batch]) => (batch as StoredEvent[]).map((e) => e.type));
    expect(backfilled).toEqual(['project.created', 'agent.created']);
    expect(sent.filter(([id, ch]) => id === 9 && ch === 'desk:events')).toHaveLength(1);
    const [live] = store.append({ project_id: p, agent_id: null, type: 'message.user', payload: { text: 'hi' } });
    stream().onEvent(live as StoredEvent);
    stream().onEvent(live as StoredEvent);
    const [elsewhere] = store.append({ project_id: other, agent_id: null, type: 'message.user', payload: { text: 'x' } });
    stream().onEvent(elsewhere as StoredEvent);
    stream().onEphemeral?.({ type: 'assistant.delta', project_id: p, agent_id: 'd', payload: { run_id: 'r', text: 'Hel' } });
    const forwarded = sent.filter(([id]) => id === 9).map(([, ch, e]) => (ch === 'desk:event' ? (e as StoredEvent).id : ch));
    expect(forwarded).toEqual(['desk:events', live!.id, 'desk:ephemeral']);
    broker!.dropSender(9);
    const [later] = store.append({ project_id: p, agent_id: null, type: 'message.user', payload: { text: 'again' } });
    stream().onEvent(later as StoredEvent);
    expect(sent.filter(([id]) => id === 9)).toHaveLength(forwarded.length);
  });

  it('backfills every watch from its own cursor after a reconnect, so events appended offline arrive once', async () => {
    const { runtime, sent, stream, store, deps } = await setup();
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    const deskId = store.list({ projectId: p, types: ['agent.created'] })[0]!.agent_id!;
    await broker!.start();
    await broker!.watch(9, p, 0);
    const delivered = () =>
      sent
        .filter(([id]) => id === 9)
        .flatMap(([, ch, x]) => (ch === 'desk:events' ? (x as StoredEvent[]) : ch === 'desk:event' ? [x as StoredEvent] : []))
        .map((e) => e.id);

    // The daemon goes away (daemon.json is gone), so the stream's reconnect sends the broker offline.
    let online = false;
    const { connect, credentials } = deps;
    deps.connect = () => (online ? connect() : null);
    deps.credentials = () => (online ? credentials() : null);
    stream().onStatus?.('reconnecting');
    expect(broker!.snapshot().connection.status).toBe('offline');
    // What a graceful shutdown appends after the server has closed.
    const [finished] = store.append({ project_id: p, agent_id: deskId, type: 'run.finished', payload: { run_id: 'r1', reason: 'error', detail: 'daemon_shutdown' } });

    online = true;
    await until(() => delivered().includes(finished!.id));
    // The new stream starts after the daemon's seq; a replay of the same event is still dropped.
    stream().onEvent(finished as StoredEvent);
    const [live] = store.append({ project_id: p, agent_id: null, type: 'message.user', payload: { text: 'back' } });
    stream().onEvent(live as StoredEvent);
    expect(delivered().filter((id) => id === finished!.id)).toHaveLength(1);
    expect(delivered().at(-1)).toBe(live!.id);
  });

  it('tracks skill runtime progress for every window and bumps a counter when a runtime changes state', async () => {
    const { stream, store } = await setup();
    await broker!.start();
    stream().onEphemeral?.({ type: 'skill.runtime_progress', project_id: '_global', agent_id: null, payload: { scope: 'global', name: 'paper-lookup', step: 'Installing 1 Python package' } });
    stream().onEphemeral?.({ type: 'skill.runtime_progress', project_id: 'p1', agent_id: null, payload: { scope: 'project', name: 'pretty-mermaid', step: 'Installing elkjs', done: 3, total: 16 } });
    expect(broker!.snapshot().runtimes.progress).toEqual({
      'global:paper-lookup': { step: 'Installing 1 Python package' },
      'project:p1:pretty-mermaid': { step: 'Installing elkjs', done: 3, total: 16 },
    });
    const [ready] = store.append({ project_id: '_global', agent_id: null, type: 'skill.runtime_changed', payload: { scope: 'global', name: 'paper-lookup', state: 'ready', reason: null } });
    stream().onEvent(ready as StoredEvent);
    expect(broker!.snapshot().runtimes).toEqual({ progress: { 'project:p1:pretty-mermaid': { step: 'Installing elkjs', done: 3, total: 16 } }, seq: 1 });
  });

  it('goes offline without daemon.json, polls, and reconnects when it appears', async () => {
    let available = false;
    const { runtime } = await setup();
    runtime.createProject({ name: 'Launch', goal: 'g' });
    const real = new DeskClient({ baseUrl: `http://127.0.0.1:${server!.port}`, token: 't' });
    broker!.stop();
    broker = new Broker({
      connect: () => (available ? real : null),
      credentials: () => (available ? real.credentials() : null),
      send: () => {},
      broadcast: () => {},
      hello: () => ({ client: 'desktop', notifications: false }),
      streamFactory: () => ({ start: () => {}, close: () => {} }),
      pollMs: 5,
    });
    await broker.start();
    expect(broker.snapshot().connection.status).toBe('offline');
    expect(() => broker!.getClient()).toThrow(/not running/);
    available = true;
    await until(() => broker!.snapshot().overview.length === 1);
    expect(broker.snapshot().connection.status).toBe('connecting');
  });

  it('blocks on a protocol mismatch', async () => {
    await setup();
    const bad = new DeskClient({
      baseUrl: 'http://x',
      token: 't',
      fetch: async () => new Response(JSON.stringify({ version: '9.0.0', protocol_version: 99 }), { headers: { 'content-type': 'application/json' } }),
    });
    broker!.stop();
    broker = new Broker({ connect: () => bad, credentials: () => bad.credentials(), send: () => {}, broadcast: () => {}, hello: () => ({ client: 'desktop', notifications: false }) });
    await broker.start();
    expect(broker.snapshot().connection).toMatchObject({ status: 'mismatch', detail: expect.stringContaining('protocol 99') });
  });

  it('streams real events end to end with the default DeskStream', async () => {
    const { runtime, sent } = await setup({ streamFactory: undefined });
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    await broker!.start();
    await until(() => broker!.snapshot().connection.status === 'live');
    await broker!.watch(3, p, 0);
    runtime.sendToDesk(p, 'hello');
    await until(() => sent.some(([, ch, e]) => ch === 'desk:event' && (e as StoredEvent).type === 'assistant.message'));
    expect(sent.some(([, ch]) => ch === 'desk:ephemeral')).toBe(true);
    await runtime.shutdown();
  });
});
