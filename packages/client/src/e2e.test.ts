import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools } from '@desk/fake-model';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import { DeskClient, DeskStream, applyChatDelta, emptyChat, emptyTimeline, projectFromOverview, reduceChat, reduceProject, reduceTimeline } from './index';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

describe('folding a real Desk turn', () => {
  it('produces the chat, roster and line diagram a UI would show', async () => {
    h = await createHarness({
      script: (req) =>
        req.model === FAKE_MODEL.id
          ? text('thread idle')
          : req.messages.some((m) => m.role === 'tool')
            ? text('Dispatched one scout.')
            : tools(call('spawn_thread', { title: 'Scout', brief: 'look around' })),
    });
    const runtime = newRuntime(h);
    server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: 'v' }), store: h.store, token: 't', port: 0 });
    const client = new DeskClient({ baseUrl: `http://127.0.0.1:${server.port}`, token: 't' });
    const created = await client.projects.create({ name: 'P', goal: 'g', settings: { thread_model: FAKE_MODEL.id } });
    let project = projectFromOverview(await client.projects.get(created.project.id));
    let chat = emptyChat(project.desk!.id);
    let timeline = emptyTimeline(project.desk!.id);
    const stream = new DeskStream({
      credentials: () => client.credentials(),
      projectId: project.project.id,
      afterSeq: 0,
      onEvent: (e) => {
        project = reduceProject(project, e);
        chat = reduceChat(chat, e);
        timeline = reduceTimeline(timeline, e);
      },
      onEphemeral: (e) => (chat = applyChatDelta(chat, e)),
    });
    stream.start();
    await client.projects.send(project.project.id, 'Look around');
    const end = Date.now() + 5000;
    while (!chat.items.some((i) => i.kind === 'assistant' && i.text === 'Dispatched one scout.')) {
      if (Date.now() > end) throw new Error(`timed out; chat: ${JSON.stringify(chat.items)}`);
      await new Promise((r) => setTimeout(r, 20));
    }
    expect(chat.items[0]).toMatchObject({ kind: 'user', text: 'Look around' });
    expect(chat.items.some((i) => i.kind === 'tools' && i.calls[0]!.name === 'spawn_thread')).toBe(true);
    expect(project.threads.map((t) => t.title)).toEqual(['Scout']);
    expect(timeline.stations.map((s) => s.kind).slice(0, 2)).toEqual(['brief', 'dispatch']);
    expect(timeline.lanes.map((l) => l.title)).toEqual(['Scout']);
    stream.close();
    await runtime.shutdown();
  });
});
