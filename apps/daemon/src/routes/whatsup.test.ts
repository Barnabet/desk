import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools } from '@desk/fake-model';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp } from '../app';

let h: Harness;
afterEach(async () => h?.cleanup());

describe("GET /projects/:id What's up", () => {
  it("carries Desk's latest What's up, and null before it has written one", async () => {
    h = await createHarness({ script: (req) => (req.messages.at(-1)?.role === 'tool' ? text('') : tools(call('update_whats_up', { text: 'Scoping the relaunch.' }))) });
    const runtime = newRuntime(h);
    const app = createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0', saveModels: () => {} });
    const api = async (method: string, path: string, body?: unknown) =>
      (await app.request(`/v1${path}`, { method, headers: { authorization: 'Bearer t', 'content-type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) })).json() as Promise<any>;
    const { project } = await api('POST', '/projects', { name: 'Demo', goal: 'g' });
    expect((await api('GET', `/projects/${project.id}`)).whats_up).toBeNull();
    await api('POST', `/projects/${project.id}/messages`, { text: 'Go' });
    await runtime.whenIdle();
    expect((await api('GET', `/projects/${project.id}`)).whats_up).toMatchObject({ text: 'Scoping the relaunch.', ts: expect.any(String) });
  });
});
