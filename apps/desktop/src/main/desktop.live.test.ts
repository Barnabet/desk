import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { DeskClient } from '@desk/client';
import { startDaemon } from '@desk/daemon';
import { initialGlobalState } from '@desk/bff/contract';
import { dispatch, type HandlerContext } from '@desk/bff/server';

/** The desktop app's IPC layer against a real deskd and the configured model endpoint (`pnpm test:live`). */
describe('live: desktop IPC against deskd and the model endpoint', () => {
  it('tests the endpoint, creates a project and gets a reply from Desk', async () => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-desktop-')));
    const daemon = await startDaemon({ dataDir: join(dir, 'data'), port: 0 });
    const client = new DeskClient({ baseUrl: `http://127.0.0.1:${daemon.port}`, token: daemon.token });
    const noop = async () => {
      throw new Error('not used');
    };
    const ctx: HandlerContext = {
      senderId: 1,
      client: () => client,
      broker: { snapshot: () => initialGlobalState(), watch: async () => {}, unwatch: () => {} },
      daemon: { status: noop, start: noop, restart: noop, stop: noop, repair: noop },
      app: {
        info: () => ({ version: '1.0.0', platform: process.platform, packaged: false, dataDir: join(dir, 'data') }),
        openExternal: noop,
        pickFolder: async () => null,
        revealLogs: noop,
        saveFile: async () => false,
        openMain: () => {},
        settings: () => ({ notifications: false }),
        updateSettings: () => ({ notifications: false }),
      },
    };
    try {
      const test = await dispatch('config.testEndpoint', {}, ctx);
      expect(test).toMatchObject({ ok: true, value: { ok: true } });

      const created = await dispatch('projects.create', { name: 'Live desktop smoke', goal: 'Check the desktop wiring' }, ctx);
      if (!created.ok) throw new Error(created.error.message);
      const id = (created.value as { project: { id: string } }).project.id;
      const sent = await dispatch('projects.send', { id, text: 'No threads are needed. Reply with one short sentence that contains the word "ready".' }, ctx);
      expect(sent.ok).toBe(true);
      await daemon.runtime.whenIdle();

      const chat = await dispatch('projects.chat', { id }, ctx);
      if (!chat.ok) throw new Error(chat.error.message);
      const replies = (chat.value as { events: Array<{ type: string; payload: { content?: string | null } }> }).events
        .filter((e) => e.type === 'assistant.message')
        .map((e) => e.payload.content ?? '');
      expect(replies.join(' ').toLowerCase()).toContain('ready');
    } finally {
      await daemon.stop();
      await rm(dir, { recursive: true, force: true });
    }
  });
});
