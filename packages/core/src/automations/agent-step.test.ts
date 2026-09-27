import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { call, text, tools, type ChatRequest } from '@desk/fake-model';
import { DEFAULT_POLICY, type StoredEvent } from '@desk/protocol';
import { ConflictError, ValidationError } from '../errors';
import { automationHarness, type FakeClock } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { getAgent, listApprovals } from '../state/queries';
import { getAutomation, getRun, getStepRun, stepAgentsOf } from './queries';
import { activeMs } from './engine';
import { stepDir } from './folders';
import { runDetail } from './views';

let h: Harness;
let rt: Runtime;
let projectId: string;
let clock: FakeClock;
afterEach(async () => h?.cleanup());

const system = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
const isStep = (req: ChatRequest) => system(req).includes('You are one step of the automation');
const agentDef = (extra: object = {}, top: object = {}) => ({
  title: 'Summarise',
  inputs: [{ key: 'topic', label: 'Topic', type: 'text' }],
  steps: [{ id: 'sum', title: 'Summarise', kind: 'agent', brief: 'Write about {{inputs.topic}}.', output_keys: [{ key: 'headline', description: 'the headline' }], ...extra }],
  ...top,
});
const create = (def: object, name = 'a') => rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
const start = (id: string, inputs: object = { topic: 'AI' }) => rt.engine.startRun(id, { trigger: 'manual', test: false, inputs: inputs as never, by: 'user' });

describe('agent steps', () => {
  it('runs an agent with the rendered brief, settles on complete and archives the agent', async () => {
    ({ h, rt, projectId, clock } = await automationHarness({ script: (req) => (isStep(req) ? tools(call('complete', { summary: 'Done', outputs: { headline: 'AI wins' } })) : text('ok')) }));
    const r = await start(create(agentDef()));
    await rt.whenIdle();
    const row = getStepRun(h.store.db, r, 'sum')!;
    expect(row).toMatchObject({ status: 'succeeded', outputs: { headline: 'AI wins' }, summary: 'Done' });
    expect(system(h.fake.requests[0]!)).toContain('Write about AI.');
    expect(getRun(h.store.db, r)!.status).toBe('succeeded');
    await vi.waitFor(() => expect(getAgent(h.store.db, row.agent_id!)!.archived_at).not.toBeNull());
    expect(existsSync(stepDir(h.dir, r, 'sum'))).toBe(true); // a plain step folder stays for the run's files
  });

  it('retries with a fresh agent and archives the failed one', async () => {
    let n = 0;
    ({ h, rt, projectId, clock } = await automationHarness({
      script: (req) => {
        if (!isStep(req)) return text('ok');
        n++;
        return n === 1 ? tools(call('fail_step', { reason: 'flaky source' })) : tools(call('complete', { summary: 'second time', outputs: { headline: 'ok' } }));
      },
    }));
    const r = await start(create(agentDef({ on_error: { retry: 1 } })));
    await rt.whenIdle();
    expect(getStepRun(h.store.db, r, 'sum')).toMatchObject({ status: 'pending', attempt: 2 });
    clock.advance(30_000);
    await rt.engine.tick();
    await rt.whenIdle();
    expect(getStepRun(h.store.db, r, 'sum')).toMatchObject({ status: 'succeeded', attempt: 2, summary: 'second time' });
    const agents = stepAgentsOf(h.store.db, r, 'sum');
    expect(agents).toHaveLength(2);
    await vi.waitFor(() => expect(agents.every((a) => getAgent(h.store.db, a.id)!.archived_at)).toBe(true));
  });

  it('nudges once after a turn without tools, then fails the step', async () => {
    ({ h, rt, projectId, clock } = await automationHarness({ script: (req) => (isStep(req) ? text('I think it is done.') : text('ok')) }));
    const r = await start(create(agentDef()));
    await rt.whenIdle();
    const stepReqs = h.fake.requests.filter(isStep);
    expect(stepReqs).toHaveLength(2);
    expect(stepReqs[1]!.messages.at(-1)!.content).toMatch(/^\[Desk runtime — reminder\] Call complete/);
    expect(getStepRun(h.store.db, r, 'sum')).toMatchObject({ status: 'failed', error: 'Ended its turn twice without calling complete or fail_step' });
  });

  it('fails at the step limit and on a timeout', async () => {
    ({ h, rt, projectId, clock } = await automationHarness({ script: (req) => (isStep(req) ? tools(call('list_dir', { path: '.' })) : text('ok')) }));
    const r = await start(create(agentDef({ max_steps: 2 })));
    await rt.whenIdle();
    expect(getStepRun(h.store.db, r, 'sum')!.error).toBe('Reached the step limit (2) without completing');

    h.fake.setScript((req) => (isStep(req) ? { kind: 'hang' } : text('ok')));
    const r2 = await start(create(agentDef({ timeout_min: 1 }), 'b'));
    await vi.waitFor(() => expect(getStepRun(h.store.db, r2, 'sum')!.agent_id).toBeTruthy());
    vi.spyOn(rt, 'stepActiveMs').mockReturnValue(61_000);
    await rt.engine.tick();
    expect(getStepRun(h.store.db, r2, 'sum')!.error).toBe('Timed out after 1 minutes of work');
    await rt.whenIdle();
    expect(getAgent(h.store.db, getStepRun(h.store.db, r2, 'sum')!.agent_id!)!.status).toBe('cancelled');
  });

  it('measures active time from run boundaries', () => {
    const ev = (type: string, ts: string) => ({ id: 1, project_id: 'p', agent_id: 'a', type, ts, payload: { run_id: 'x' } }) as unknown as StoredEvent;
    const events = [ev('run.started', '2026-09-28T06:00:00.000Z'), ev('run.finished', '2026-09-28T06:05:00.000Z'), ev('run.started', '2026-09-28T07:00:00.000Z')];
    expect(activeMs(events, Date.parse('2026-09-28T07:02:00.000Z'))).toBe(7 * 60_000);
  });

  it('asks the user (never Desk) through grants and policy, and remembers an approval', async () => {
    ({ h, rt, projectId, clock } = await automationHarness({
      script: (req) => {
        if (!isStep(req)) return text('ok');
        const results = req.messages.filter((m) => m.role === 'tool');
        return results.length ? tools(call('complete', { summary: 'ran it', outputs: { headline: 'x' } })) : tools(call('bash', { command: 'echo hi' }));
      },
    }));
    rt.updateSettings(projectId, { policy: [{ tool: 'bash', match: { command: '^echo' }, action: 'ask', delegate_to_desk: true }, ...DEFAULT_POLICY] });
    const id = create(agentDef());
    const r = await start(id);
    await rt.whenIdle();
    const [ap] = listApprovals(h.store.db, projectId, 'pending');
    expect(ap).toMatchObject({ tool: 'bash', delegate_to_desk: false });
    expect(runDetail(h.store.db, r).status).toBe('waiting');
    await expect(rt.resolveApproval(ap!.id, 'denied', { remember: true })).rejects.toThrow(ValidationError);
    await rt.resolveApproval(ap!.id, 'approved', { remember: true });
    expect(h.store.list({ projectId, types: ['automation.grants_set'] }).at(-1)!.payload).toMatchObject({ reason: 'remembered', source: { run_id: r, step_id: 'sum' } });
    await rt.whenIdle();
    expect(getStepRun(h.store.db, r, 'sum')!.status).toBe('succeeded');
    expect(getAutomation(h.store.db, id)!.grants).toEqual([{ tool: 'bash', match: { command: '^echo hi$' }, action: 'allow' }]);
    const r2 = await start(id);
    await rt.whenIdle();
    expect(listApprovals(h.store.db, projectId, 'pending')).toEqual([]);
    expect(getStepRun(h.store.db, r2, 'sum')!.status).toBe('succeeded');
    // Suspended grants: the approval comes back, and remembering is refused.
    rt.automations.save(id, agentDef({ title: 'Summarise again' }), { origin: 'agent:d', via: 'tool' });
    const r3 = await start(id);
    await rt.whenIdle();
    const [ap3] = listApprovals(h.store.db, projectId, 'pending');
    await expect(rt.resolveApproval(ap3!.id, 'approved', { remember: true })).rejects.toThrow(ConflictError);
    await rt.resolveApproval(ap3!.id, 'approved');
    await rt.whenIdle();
    expect(getStepRun(h.store.db, r3, 'sum')!.status).toBe('succeeded');
  });

  it('refuses to remember a thread approval', async () => {
    ({ h, rt, projectId, clock } = await automationHarness({ script: [tools(call('bash', { command: 'echo hi' })), text('done')] }));
    rt.updateSettings(projectId, { policy: [{ tool: 'bash', action: 'ask' }] });
    const thread = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w') });
    rt.sendMessage(thread, 'go');
    await rt.whenIdle();
    const [ap] = listApprovals(h.store.db, projectId, 'pending');
    await expect(rt.resolveApproval(ap!.id, 'approved', { remember: true })).rejects.toThrow(/automation step/);
  });

  it('stops the agent when its run is cancelled', async () => {
    ({ h, rt, projectId, clock } = await automationHarness({ script: (req) => (isStep(req) ? { kind: 'hang' } : text('ok')) }));
    const r = await start(create(agentDef()));
    await vi.waitFor(() => expect(getStepRun(h.store.db, r, 'sum')!.agent_id).toBeTruthy());
    await rt.engine.cancelRun(r, 'enough');
    await rt.whenIdle();
    const agentId = getStepRun(h.store.db, r, 'sum')!.agent_id!;
    expect(getAgent(h.store.db, agentId)!.status).toBe('cancelled');
    await vi.waitFor(() => expect(getAgent(h.store.db, agentId)!.archived_at).not.toBeNull());
  });

  it('works in a git worktree of a source and removes it when the run ends', async () => {
    ({ h, rt, projectId, clock } = await automationHarness({ script: (req) => (isStep(req) ? tools(call('complete', { summary: 'committed', outputs: { headline: 'x' } })) : text('ok')) }));
    const repo = join(h.files, 'repo');
    mkdirSync(repo);
    execFileSync('git', ['init', '-q', repo]);
    writeFileSync(join(repo, 'README.md'), '# r');
    execFileSync('git', ['-C', repo, 'add', '.']);
    execFileSync('git', ['-C', repo, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'init']);
    const source = await rt.addSource(projectId, repo);
    const r = await start(create(agentDef({ git_source_id: source }), 'gitty'));
    // The worktree is set up asynchronously, after the step records its agent's id.
    await vi.waitFor(() => expect(getAgent(h.store.db, getStepRun(h.store.db, r, 'sum')!.agent_id!)).toBeDefined());
    await rt.whenIdle();
    const agent = getAgent(h.store.db, getStepRun(h.store.db, r, 'sum')!.agent_id!)!;
    expect(agent.git_branch).toMatch(/^desk\/auto-gitty-[a-z0-9]{6}$/);
    await vi.waitFor(() => expect(getAgent(h.store.db, agent.id)!.archived_at).not.toBeNull());
    expect(existsSync(stepDir(h.dir, r, 'sum'))).toBe(false);
    expect(execFileSync('git', ['-C', repo, 'branch', '--list', agent.git_branch!]).toString()).toContain(agent.git_branch!);
  });
});
