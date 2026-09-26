// apps/daemon/src/routes/automations.test.ts
import { mkdirSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest } from '@desk/fake-model';
import { logFile, stepDir } from '@desk/core';
import { automationHarness, askDef, waitDef, type Harness } from '@desk/core/testing';
import { createApp } from '../app';

let h: Harness;
afterEach(async () => h?.cleanup());

const system = (req: ChatRequest) => String(req.messages[0]?.content ?? '');

async function setup() {
  const r = await automationHarness({ script: (req) => (system(req).includes('You are one step') ? tools(call('bash', { command: 'echo hi' })) : text('ok')) });
  h = r.h;
  const app = createApp({ runtime: r.rt, store: h.store, models: h.models, token: 't', version: '1.0.0', saveModels: () => {} });
  const call_ = async (method: string, path: string, body?: unknown) => {
    const res = await app.request(`/v1${path}`, { method, headers: { authorization: 'Bearer t', 'content-type': 'application/json' }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
    const type = res.headers.get('content-type') ?? '';
    return { status: res.status, body: type.includes('json') ? ((await res.json()) as any) : await res.text() };
  };
  return { ...r, api: call_ };
}

describe('automation routes', () => {
  it('creates, validates, saves with a base version, and answers 400/409/404', async () => {
    const { api, projectId } = await setup();
    const created = await api('POST', `/projects/${projectId}/automations`, { name: 'digest', definition: waitDef(), change_note: 'first', via: 'editor' });
    expect(created.status).toBe(201);
    const id = created.body.automation.id;
    expect(created.body).toMatchObject({ automation: { name: 'digest', version: 1, enabled: false, definition: { title: 'Wait a bit' } }, warnings: [] });
    const bad = await api('POST', `/projects/${projectId}/automations`, { name: 'bad', definition: { title: 'x', steps: [] } });
    expect(bad.status).toBe(400);
    expect(bad.body.error.details.errors[0]).toMatchObject({ path: 'steps' });
    expect((await api('POST', `/projects/${projectId}/automations/validate`, { definition: waitDef({ triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'Europe/Paris', catch_up: 'once' }] }) })).body).toMatchObject({ errors: [], next_times: { '0': expect.any(Array) } });
    const saved = await api('PUT', `/automations/${id}`, { definition: waitDef({ title: 'Wait longer' }), base_version: 1, via: 'editor' });
    expect(saved.body.automation.version).toBe(2);
    const stale = await api('PUT', `/automations/${id}`, { definition: waitDef(), base_version: 1 });
    expect(stale.status).toBe(409);
    expect(stale.body.error.details).toEqual({ current_version: 2 });
    expect((await api('GET', '/automations/nope')).status).toBe(404);
    expect((await api('GET', `/projects/${projectId}/automations`)).body).toEqual([expect.objectContaining({ name: 'digest', title: 'Wait longer', version: 2 })]);
    expect(h.store.list({ projectId, types: ['automation.saved'] }).map((e) => (e.payload as { via: string }).via)).toEqual(['editor', 'editor']);
  });

  it('keeps history, restores, switches, sets grants, exports and imports, lays out and deletes', async () => {
    const { api, projectId } = await setup();
    const id = (await api('POST', `/projects/${projectId}/automations`, { name: 'digest', definition: waitDef() })).body.automation.id;
    await api('PUT', `/automations/${id}`, { definition: waitDef({ title: 'Two' }), base_version: 1 });
    expect((await api('GET', `/automations/${id}/versions`)).body.map((v: any) => v.version)).toEqual([2, 1]);
    expect((await api('GET', `/automations/${id}/versions/1`)).body).toMatchObject({ version: 1, definition: { title: 'Wait a bit' } });
    expect((await api('POST', `/automations/${id}/versions/1/restore`)).body).toMatchObject({ version: 3, title: 'Wait a bit' });
    expect((await api('PUT', `/automations/${id}/enabled`, { enabled: true })).body.enabled).toBe(true);
    const grants = [{ tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' }];
    expect((await api('PUT', `/automations/${id}/grants`, { grants, reason: 'enabled' })).body.grants).toEqual(grants);
    expect(h.store.list({ projectId, types: ['automation.grants_set'] }).at(-1)!.payload).toMatchObject({ reason: 'enabled' });
    expect((await api('POST', `/automations/${id}/grants/keep`)).status).toBe(200);
    expect((await api('PUT', `/automations/${id}/layout`, { layout: { w: { x: 10, y: 20 } } })).status).toBe(200);
    expect((await api('GET', `/automations/${id}`)).body.layout).toEqual({ w: { x: 10, y: 20 } });
    const exp = (await api('GET', `/automations/${id}/export`)).body;
    expect(exp).toEqual({ format: 'desk-automation/1', name: 'digest', definition: expect.objectContaining({ title: 'Wait a bit' }) });
    expect((await api('POST', `/projects/${projectId}/automations/import`, exp)).status).toBe(409);
    const imported = await api('POST', `/projects/${projectId}/automations/import`, { ...exp, name: 'digest-copy' });
    expect(imported.status).toBe(201);
    expect(imported.body.automation).toMatchObject({ name: 'digest-copy', enabled: false, grants: [] });
    expect((await api('DELETE', `/automations/${id}`)).status).toBe(200);
    expect((await api('GET', `/automations/${id}`)).status).toBe(404);
  });

  it('runs, lists runs, reads a run, answers, cancels, and serves logs and files', async () => {
    const { api, projectId, rt } = await setup();
    const id = (await api('POST', `/projects/${projectId}/automations`, { name: 'ask', definition: askDef() })).body.automation.id;
    const started = await api('POST', `/automations/${id}/runs`, { inputs: {} });
    expect(started.status).toBe(202);
    const runId = started.body.run_id;
    const detail = (await api('GET', `/automation-runs/${runId}`)).body;
    expect(detail).toMatchObject({ id: runId, status: 'waiting', automation_name: 'ask', steps: expect.arrayContaining([expect.objectContaining({ step_id: 'ask', status: 'waiting', question: expect.objectContaining({ text: 'Publish?' }) })]) });
    expect((await api('POST', `/automation-runs/${runId}/steps/ask/answer`, { decision: 'approve', note: 'go' })).status).toBe(200);
    expect((await api('GET', `/automation-runs/${runId}`)).body.status).toBe('succeeded');
    expect((await api('POST', `/automation-runs/${runId}/steps/ask/answer`, { decision: 'approve' })).status).toBe(409);

    const second = (await api('POST', `/automations/${id}/runs`, { inputs: {}, test: true })).body.run_id;
    expect((await api('POST', `/automation-runs/${second}/cancel`)).status).toBe(200);
    const runs = (await api('GET', `/automations/${id}/runs?limit=10`)).body;
    expect(runs.map((e: any) => [e.kind, e.run.id, e.run.status])).toEqual([
      ['run', second, 'cancelled'],
      ['run', runId, 'succeeded'],
    ]);

    // The run folder, confined; the log from deskd's logs folder.
    const dir = stepDir(h.dir, runId, 'yes');
    writeFileSync(join(dir, 'note.txt'), 'hello');
    symlinkSync('/etc/hosts', join(dir, 'escape.txt'));
    mkdirSync(join(h.dir, 'automation-runs', runId, 'logs'), { recursive: true });
    writeFileSync(logFile(h.dir, runId, 'yes'), 'the log');
    expect((await api('GET', `/automation-runs/${runId}/files?path=steps/yes`)).body.map((e: any) => e.name)).toEqual(['escape.txt', 'note.txt']);
    expect((await api('GET', `/automation-runs/${runId}/files/raw/steps/yes/note.txt`)).body).toBe('hello');
    expect((await api('GET', `/automation-runs/${runId}/files/raw/steps/yes/escape.txt`)).status).toBe(403);
    expect((await api('GET', `/automation-runs/${runId}/files/raw/../../daemon.json`)).status).toBeGreaterThanOrEqual(400);
    expect((await api('GET', `/automation-runs/${runId}/steps/yes/log`)).body).toBe('the log');
    expect((await api('GET', `/automation-runs/${runId}/steps/no/log`)).status).toBe(404);
    void rt;
  });

  it("serves a step agent's transcript, and remembers an approval as a grant", async () => {
    const { api, projectId, rt } = await setup();
    const id = (await api('POST', `/projects/${projectId}/automations`, { name: 'echo', definition: { title: 'Echo', steps: [{ id: 'say', title: 'Say hi', kind: 'agent', brief: 'Say hi.' }] } })).body.automation.id;
    const runId = (await api('POST', `/automations/${id}/runs`, { inputs: {} })).body.run_id;
    await rt.whenIdle();
    const t = (await api('GET', `/automation-runs/${runId}/steps/say/transcript`)).body;
    expect(t.events.some((e: any) => e.type === 'tool.call')).toBe(true);
    const [ap] = (await api('GET', `/projects/${projectId}/approvals?status=pending`)).body;
    expect((await api('POST', `/approvals/${ap.id}/resolve`, { decision: 'approved', remember: true })).status).toBe(200);
    expect((await api('GET', `/automations/${id}`)).body.grants).toEqual([{ tool: 'bash', match: { command: '^echo hi$' }, action: 'allow' }]);
    expect((await api('GET', `/automation-runs/${runId}/steps/nope/transcript`)).status).toBe(404);
  });
});
