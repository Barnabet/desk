import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest } from '@desk/fake-model';
import { buildToolContext } from '../agent/context';
import { logFile, runDir } from '../automations/folders';
import { getAutomation, getRun } from '../automations/queries';
import { deskToolsFor, threadToolsFor } from '../runtime/toolsets';
import type { Runtime } from '../runtime/runtime';
import type { AgentRow } from '../state/queries';
import { automationHarness } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { ToolContext } from './types';
import {
  automationCancelTool,
  automationDeleteTool,
  automationListTool,
  automationReadRunTool,
  automationReadTool,
  automationRequestEnableTool,
  automationRunTool,
  automationSaveTool,
  automationTestTool,
  deskAutomationTools,
  waitForRunTool,
  whatItDoes,
} from './automations';

let h: Harness;
let rt: Runtime;
let projectId: string;
let desk: AgentRow;
let ctx: ToolContext;
afterEach(async () => h?.cleanup());

const system = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
async function setup() {
  ({ h, rt, projectId, desk } = await automationHarness({
    script: (req) => (system(req).includes('You are one step') ? tools(call('complete', { summary: 'Wrote the digest', outputs: { headline: 'Big news' } })) : text('ok')),
  }));
  ctx = buildToolContext(desk, 'run', 'tc', new AbortController().signal, { sandboxEnabled: false, jobs: rt.jobs, services: rt.services });
}
/** Tool output as text (a yield's content included). */
const run = async (tool: { execute(i: never, c: ToolContext): Promise<unknown> }, input: object): Promise<string> => {
  const out = (await tool.execute(input as never, ctx)) as string | { content: string };
  return typeof out === 'string' ? out : out.content;
};
const deskTexts = () => h.store.list({ agentId: desk.id, types: ['message.agent'] }).map((e) => (e.type === 'message.agent' ? e.payload.text : ''));

const digest = (extra: object = {}) => ({
  title: 'Weekly digest',
  description: 'Summarises the week.',
  inputs: [{ key: 'topic', label: 'Topic', type: 'text', default: 'AI' }],
  triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'Europe/Paris', catch_up: 'once' }],
  steps: [
    { id: 'sum', title: 'Summarise', kind: 'agent', brief: 'Summarise {{inputs.topic}}.', output_keys: [{ key: 'headline', description: 'the headline' }], publish: ['*.md'] },
    { id: 'ok', title: 'Publish?', kind: 'ask', question: 'Publish "{{steps.sum.outputs.headline}}"?' },
  ],
  edges: [{ from: 'sum', to: 'ok' }],
  ...extra,
});

describe("Desk's automation tools", () => {
  it('creates, refuses blind or stale updates, updates with base_version, and lists every error', async () => {
    await setup();
    expect(await run(automationSaveTool, { name: 'digest', definition: digest(), change_note: 'first' })).toMatch(/^Saved digest v1\.[\s\S]*Test it with automation_test before proposing it\.$/);
    expect(getAutomation(h.store.db, rt.automations.requireByName(projectId, 'digest').id)!.version).toBe(1);
    await expect(run(automationSaveTool, { name: 'digest', definition: digest(), change_note: 'again' })).rejects.toThrow(/digest exists \(v1\).*base_version 1/);
    expect(await run(automationSaveTool, { name: 'digest', definition: digest({ title: 'Digest' }), change_note: 'title', base_version: 1 })).toMatch(/^Saved digest v2\./);
    await expect(run(automationSaveTool, { name: 'digest', definition: digest(), change_note: 'stale', base_version: 1 })).rejects.toThrow(/"digest" is at version 2; your changes were made on version 1/);
    await expect(
      run(automationSaveTool, { name: 'bad', definition: digest({ steps: [{ id: 'a', title: 'A', kind: 'agent', brief: '{{steps.nope.summary}}' }, { id: 'a', title: 'B', kind: 'wait', minutes: 1 }], edges: [] }), change_note: 'x' }),
    ).rejects.toThrow(/^The automation is not valid:\n- steps\[\d\]\S*: .+\n- /);
    expect(h.store.list({ projectId, types: ['automation.saved'] }).at(-1)!.payload).toMatchObject({ origin: `agent:${desk.id}`, via: 'tool', change_note: 'title' });
  });

  it('lists and reads automations', async () => {
    await setup();
    await run(automationSaveTool, { name: 'digest', definition: digest(), change_note: 'first' });
    const list = await run(automationListTool, {});
    expect(list).toContain('- digest "Weekly digest": off · v1 (not tested) · schedules: 0 8 * * 1 (Europe/Paris) · last run: none');
    const read = await run(automationReadTool, { name: 'digest' });
    expect(read).toMatch(/^digest v1 · off · grants: 0 · not tested\n/);
    expect(read).toContain('"title": "Weekly digest"');
    expect(read).toContain('Last runs: none');
    await expect(run(automationReadTool, { name: 'nope' })).rejects.toThrow(/Unknown automation: nope/);
  });

  it('tests a run, says what it really does, waits for it and gets the report', async () => {
    await setup();
    await run(automationSaveTool, { name: 'digest', definition: digest(), change_note: 'first' });
    const started = await run(automationTestTool, { name: 'digest', inputs: { topic: 'robots' } });
    const runId = /test run (\S+)/.exec(started)![1]!;
    expect(started).toContain('- Summarise: an agent works with its tools');
    expect(started).toContain('- Publish?: waits for the user');
    expect(started).toContain('automations/digest/tests/');
    expect(getRun(h.store.db, runId)).toMatchObject({ test: true, trigger: 'test', by: `agent:${desk.id}`, inputs: { topic: 'robots' } });
    const waited = (await waitForRunTool.execute({ run_id: runId }, ctx)) as { yield?: { status: string } };
    expect(waited.yield?.status).toBe('waiting');
    await rt.whenIdle();
    await rt.engine.settled();
    expect(deskTexts().some((t) => t.startsWith(`Run ${runId} is waiting for the user at "Publish?"`))).toBe(true);
    await rt.engine.answer(runId, 'ok', { decision: 'approve' });
    await rt.engine.settled();
    expect(deskTexts().at(-1)).toMatch(new RegExp(`^Run ${runId} succeeded\\.\\n\\nAutomation "Weekly digest"`));
    expect(await run(waitForRunTool, { run_id: runId })).toMatch(new RegExp(`^Run ${runId} already ended\\.\\n\\nAutomation`));
  });

  it("reports a watched run the user started, and reads a run's steps", async () => {
    await setup();
    rt.saveSkill({ scope: 'project', projectId, name: 'x', description: 'test skill', instructions: 'Run', files: [{ path: 'scripts/x.py', content: 'print(1)' }] }, { projectId });
    rt.engine.register('script', ({ run: r, step }) => {
      writeFileSync(logFile(h.dir, r.id, step.id), 'line one\nthe end\n');
      rt.engine.resolveStep(r.id, step.id, { status: 'succeeded', route: null, outputs: {}, summary: 'ok' });
    });
    await run(automationSaveTool, {
      name: 'mixed',
      definition: { title: 'Mixed', steps: [{ id: 'sum', title: 'Summarise', kind: 'agent', brief: 'Do it.', output_keys: [{ key: 'headline', description: 'h' }] }, { id: 'fetch', title: 'Fetch', kind: 'script', skill: 'x', script: 'x.py' }, { id: 'ok', title: 'OK?', kind: 'ask', question: 'Go?' }], edges: [{ from: 'sum', to: 'ok' }, { from: 'fetch', to: 'ok' }] },
      change_note: 'x',
    });
    const r = await rt.engine.startRun(rt.automations.requireByName(projectId, 'mixed').id, { trigger: 'manual', test: false, inputs: {}, by: 'user' });
    expect(deskTexts()).toEqual([]); // a run the user started tells Desk nothing…
    await waitForRunTool.execute({ run_id: r }, ctx); // …until Desk watches it (the agent step is still answering)
    await rt.whenIdle();
    await rt.engine.settled();
    expect(deskTexts()[0]).toMatch(new RegExp(`^Run ${r} is waiting for the user at "OK\\?"`));
    expect(await run(automationReadRunTool, { run_id: r })).toMatch(/^Automation "Mixed" \(mixed\)/);
    expect(await run(automationReadRunTool, { run_id: r, step: 'fetch' })).toContain(`Log: ${logFile(h.dir, r, 'fetch')}\nline one\nthe end`);
    expect(await run(automationReadRunTool, { run_id: r, step: 'sum', mode: 'full' })).toContain('complete');
    expect(await run(automationReadRunTool, { run_id: r, step: 'sum' })).toContain('Summary: Wrote the digest');
    expect(await run(automationCancelTool, { run_id: r, reason: 'not needed' })).toBe(`Cancelled run ${r}.`);
    await rt.engine.settled();
    expect(deskTexts().at(-1)).toMatch(new RegExp(`^Run ${r} cancelled\\.`));
    await expect(run(automationReadRunTool, { run_id: 'nope' })).rejects.toThrow(/Unknown run: nope/);
  });

  it('runs on request, asks the user to turn it on, and deletes behind a gate', async () => {
    await setup();
    await run(automationSaveTool, { name: 'digest', definition: digest(), change_note: 'first' });
    const out = await run(automationRunTool, { name: 'digest', inputs: {} });
    expect(getRun(h.store.db, /run (\S+)/.exec(out)![1]!)).toMatchObject({ trigger: 'desk', test: false });
    const asked = await run(automationRequestEnableTool, { name: 'digest', note: 'Tested with robots' });
    expect(asked).toContain('Only the user can turn it on');
    expect(asked).toContain('v1 has no succeeded test run');
    expect(getAutomation(h.store.db, rt.automations.requireByName(projectId, 'digest').id)!.enable_request).toMatchObject({ note: 'Tested with robots' });
    expect(automationDeleteTool.gate).toMatchObject({ unmatched: 'ask' });
    expect(await run(automationDeleteTool, { name: 'digest' })).toBe('Deleted automation digest.');
    expect(() => rt.automations.requireByName(projectId, 'digest')).toThrow();
  });

  it('gives Desk the ten tools and its run folders, and threads none', async () => {
    await setup();
    const names = deskAutomationTools.map((t) => t.name);
    expect(names).toEqual(['automation_list', 'automation_read', 'automation_save', 'automation_test', 'automation_run', 'wait_for_run', 'automation_read_run', 'automation_cancel', 'automation_request_enable', 'automation_delete']);
    expect(deskToolsFor(desk).map((t) => t.name)).toEqual(expect.arrayContaining(names));
    expect(threadToolsFor(desk).some((t) => names.includes(t.name))).toBe(false);
    await run(automationSaveTool, { name: 'digest', definition: digest(), change_note: 'first' });
    const out = await run(automationTestTool, { name: 'digest', inputs: { topic: 'robots' } });
    const runId = /test run (\S+)/.exec(out)![1]!;
    await rt.whenIdle();
    // Desk reads its project's run folders (report files, Tell Desk attachments) with its own file tools.
    const inputsJson = join(runDir(h.dir, runId), 'inputs.json');
    h.fake.setScript((req) => (req.messages.at(-1)?.role === 'tool' ? text('read it') : tools(call('read_file', { path: inputsJson }))));
    rt.sendToDesk(projectId, 'Read the test inputs.');
    await rt.whenIdle();
    const read = h.store.list({ agentId: desk.id, types: ['tool.result'] }).at(-1)!;
    expect(read.type === 'tool.result' && [read.payload.name, read.payload.status]).toEqual(['read_file', 'ok']);
    expect(read.type === 'tool.result' && read.payload.content).toContain('robots');
  });

  it('describes what a test run really does', () => {
    const lines = whatItDoes('d', {
      ...digest(),
      steps: [
        { id: 's', title: 'Fetch', kind: 'script', skill: 'web-research', script: 'fetch.py', args: [], routes: [], publish: [], join: 'all', on_error: 'stop', idempotent: false },
        { id: 'w', title: 'Pause', kind: 'wait', minutes: 5, routes: [], publish: [], join: 'all', on_error: 'stop', idempotent: false },
        { id: 't', title: 'Tell', kind: 'tell_desk', text: 'x', attach: [], routes: [], publish: [], join: 'all', on_error: 'stop', idempotent: false },
        { id: 'c', title: 'Child', kind: 'automation', automation: 'other', inputs: {}, routes: [], publish: [], join: 'all', on_error: 'stop', idempotent: false },
      ],
    } as never);
    expect(lines).toEqual([
      '- Fetch: runs web-research/fetch.py for real with DESK_TEST=1 set; it acts outward unless the script checks DESK_TEST',
      '- Pause: really waits 5 min',
      '- Tell: sends you a message',
      '- Child: runs "other" as a test run too',
    ]);
  });
});
