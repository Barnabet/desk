import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest } from '@desk/fake-model';
import { automationHarness } from '../testing/automations';
import type { Harness } from '../testing/harness';
import { FAKE_MODEL } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { deskSystemPrompt } from '../agent/prompts';
import { getAgent, getDeskAgent, getProject, listThreads } from '../state/queries';
import { newId } from '../ids';
import { getStepRun } from './queries';
import { stepDir } from './folders';

let h: Harness;
let rt: Runtime;
let projectId: string;
afterEach(async () => h?.cleanup());

const system = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
const def = {
  title: 'Digest',
  description: 'Summarise competitor pages every week.',
  steps: [
    { id: 'w', title: 'Wait', kind: 'wait', minutes: 1 },
    { id: 'sum', title: 'Summarise', kind: 'agent', brief: 'Summarise the pages.', routes: ['big', 'small'], output_keys: [{ key: 'headline', description: 'the biggest change' }], max_steps: 5 },
  ],
  edges: [{ from: 'w', to: 'sum' }],
};

/** A run whose `sum` step is running (the agent executor arrives in Task 15; this seeds its state). */
async function seedRun(): Promise<{ runId: string; automationId: string }> {
  const { automation } = rt.automations.create(projectId, 'digest', def, { origin: 'user', via: 'editor' });
  const runId = 'run1';
  const e = (type: string, payload: unknown) => h.store.append({ project_id: projectId, agent_id: null, type, payload } as never);
  e('automation.run_started', { run_id: runId, automation_id: automation.id, version: 1, trigger: 'manual', test: false, inputs: {}, by: 'user', deadline_at: '2026-09-29T06:00:00.000Z' });
  e('automation.step_changed', { run_id: runId, step_id: 'w', attempt: 1, status: 'succeeded', summary: 'Waited', outputs: {} });
  e('automation.step_changed', { run_id: runId, step_id: 'sum', attempt: 1, status: 'running' });
  return { runId, automationId: automation.id };
}

/** As the engine does (Task 15): link the agent id to the step first, then create the agent. */
async function createAgent(runId: string): Promise<string> {
  const id = newId();
  h.store.append({ project_id: projectId, agent_id: null, type: 'automation.step_changed', payload: { run_id: runId, step_id: 'sum', attempt: 1, status: 'running', agent_id: id } });
  await rt.createStepAgent({ agentId: id, projectId, runId, stepId: 'sum', title: 'Summarise', brief: 'Summarise the pages.', model: FAKE_MODEL.id, skills: [], automationName: 'digest' });
  return id;
}

describe('step agents', () => {
  it('get the step prompt and toolset, and settle their step through complete', async () => {
    ({ h, rt, projectId } = await automationHarness({
      script: (req) => (system(req).includes('You are one step of the automation') ? tools(call('complete', { summary: 'Two changes', outputs: { headline: 'Acme raised prices' }, route: 'big' })) : text('ok')),
    }));
    const { runId } = await seedRun();
    const id = await createAgent(runId);
    await rt.whenIdle();
    const req = h.fake.requests[0]!;
    expect(system(req)).toContain('You are one step of the automation "Digest"');
    expect(system(req)).toContain('Nobody is watching this run');
    expect(system(req)).toContain('Summarise the pages.');
    expect(system(req)).toContain('headline — the biggest change');
    expect(system(req)).toContain('big, small');
    expect(system(req)).toContain(stepDir(h.dir, runId, 'w'));
    const names = (req.tools ?? []).map((t) => t.function.name);
    expect(names).toEqual(expect.arrayContaining(['read_file', 'write_file', 'bash', 'web_fetch', 'memory_search', 'library_read', 'skill_run', 'complete', 'fail_step']));
    for (const banned of ['message_desk', 'message_thread', 'wait_for_reply', 'list_threads', 'read_thread', 'memory_write', 'library_publish', 'service_start']) expect(names).not.toContain(banned);
    expect(req.messages.at(-1)).toEqual({ role: 'user', content: '[Desk runtime — start] Begin this step.' });
    expect(getAgent(h.store.db, id)).toMatchObject({ role: 'step', status: 'done', workspace_path: stepDir(h.dir, runId, 'sum'), parent_id: null });
    expect(getStepRun(h.store.db, runId, 'sum')).toMatchObject({ status: 'succeeded', route: 'big', outputs: { headline: 'Acme raised prices' }, summary: 'Two changes' });
  });

  it('refuses undeclared outputs and routes, then accepts a fixed call', async () => {
    let n = 0;
    ({ h, rt, projectId } = await automationHarness({
      script: (req) => {
        if (!system(req).includes('You are one step')) return text('ok');
        n++;
        if (n === 1) return tools(call('complete', { summary: 's', outputs: { other: 1 } }));
        if (n === 2) return tools(call('complete', { summary: 's', route: 'huge' }));
        return tools(call('complete', { summary: 'fine' }));
      },
    }));
    const { runId } = await seedRun();
    await createAgent(runId);
    await rt.whenIdle();
    const results = h.store.list({ types: ['tool.result'] }).map((e) => (e.type === 'tool.result' ? e.payload : null));
    expect(results[0]).toMatchObject({ status: 'error', content: expect.stringMatching(/Undeclared outputs: other/) });
    expect(results[1]).toMatchObject({ status: 'error', content: expect.stringMatching(/"huge" is not one of this step's routes \(big, small\)/) });
    expect(getStepRun(h.store.db, runId, 'sum')).toMatchObject({ status: 'succeeded', route: null, summary: 'fine' });
  });

  it('fails its step through fail_step', async () => {
    ({ h, rt, projectId } = await automationHarness({ script: (req) => (system(req).includes('You are one step') ? tools(call('fail_step', { reason: 'The pages are missing' })) : text('ok')) }));
    const { runId } = await seedRun();
    const id = await createAgent(runId);
    await rt.whenIdle();
    expect(getAgent(h.store.db, id)!.status).toBe('failed');
    expect(getStepRun(h.store.db, runId, 'sum')).toMatchObject({ status: 'failed', error: 'The pages are missing' });
  });

  it('never shows up as a thread', async () => {
    ({ h, rt, projectId } = await automationHarness({ script: () => text('ok') }));
    const { runId } = await seedRun();
    await createAgent(runId);
    expect(listThreads(h.store.db, projectId)).toEqual([]);
    const desk = getDeskAgent(h.store.db, projectId)!;
    const prompt = deskSystemPrompt({ db: h.store.db, agent: desk, project: getProject(h.store.db, projectId)!, libraryDir: rt.libraryDir(projectId) });
    expect(prompt).not.toContain('Summarise');
  });
});
