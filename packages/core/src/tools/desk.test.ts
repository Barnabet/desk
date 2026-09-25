import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type FakeReply, type Script } from '@desk/fake-model';
import type { AgentStatus, EventOf } from '@desk/protocol';
import { buildToolContext } from '../agent/context';
import { getPlan } from '../coordination/plan';
import { getAgent, getDeskAgent, listApprovals, listThreads, type AgentRow } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import {
  askUserTool,
  listThreadsTool,
  messageThreadTool,
  readThreadTool,
  reportTool,
  resolveApprovalTool,
  reviewDiffTool,
  spawnThreadTool,
  stopThreadTool,
  updatePlanTool,
  updateSettingsTool,
} from './desk';

let h: Harness;
afterEach(async () => h?.cleanup());

function routed(threadReplies: FakeReply[]): Script {
  return (req) => (req.model === FAKE_MODEL.id ? (threadReplies.shift() ?? text('(idle)')) : text('noted'));
}

async function setup(threadReplies: FakeReply[] = [], settings: Record<string, unknown> = {}) {
  h = await createHarness({ script: routed(threadReplies) });
  const rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { thread_model: FAKE_MODEL.id, ...settings } });
  const desk = getDeskAgent(h.store.db, projectId)!;
  const ctx = buildToolContext(desk, 'run', 'tc', new AbortController().signal, { sandboxEnabled: false, jobs: rt.jobs, services: rt.services });
  return { rt, projectId, desk, ctx };
}
const idOf = (out: unknown) => /thread ([0-9A-Z]{26})/.exec(String(out))![1]!;

async function gitRepo(): Promise<string> {
  const repo = join(h.files, 'repo');
  mkdirSync(repo);
  const g = (...a: string[]) => execFileSync('git', a, { cwd: repo });
  g('init', '-q', '-b', 'main');
  g('config', 'user.email', 'd@e.com');
  g('config', 'user.name', 'D');
  writeFileSync(join(repo, 'a.txt'), 'a\n');
  g('add', '.');
  g('commit', '-q', '-m', 'init');
  return repo;
}

describe('spawn_thread', () => {
  it('creates a scratch thread under Desk and starts it', async () => {
    const { rt, desk, ctx } = await setup([text('working')]);
    const id = idOf(await spawnThreadTool.execute({ title: 'Research pricing', brief: 'Compare 3 competitors' }, ctx));
    await rt.whenIdle();
    const t = getAgent(h.store.db, id)!;
    expect(t).toMatchObject({ role: 'thread', parent_id: desk.id, brief: 'Compare 3 competitors', model: FAKE_MODEL.id, git_branch: null });
    expect(existsSync(t.workspace_path!)).toBe(true);
    const first = h.fake.requests.find((r) => r.model === FAKE_MODEL.id)!;
    const [start] = h.store.list({ agentId: id, types: ['message.agent'] });
    expect(start?.type === 'message.agent' && start.payload.kind).toBe('start');
    expect(first.messages.at(-1)).toEqual({ role: 'user', content: `[message #${start!.id} from Desk — start]\n> Begin your assignment.` });
  });

  it('creates a git worktree thread for a repository source', async () => {
    const { rt, projectId, ctx } = await setup([text('ok')]);
    const sourceId = await rt.addSource(projectId, await gitRepo());
    const id = idOf(await spawnThreadTool.execute({ title: 'Fix bug', brief: 'b', git_source_id: sourceId }, ctx));
    await rt.whenIdle();
    const t = getAgent(h.store.db, id)!;
    expect(t.git_branch).toBe(`desk/fix-bug-${id.slice(-6).toLowerCase()}`);
    expect(existsSync(join(t.workspace_path!, 'a.txt'))).toBe(true);
    await expect(spawnThreadTool.execute({ title: 'x', brief: 'b', git_source_id: 'nope' }, ctx)).rejects.toThrow(/Unknown git source/);
  });
});

describe('message_thread revisions', () => {
  it('reopens a done thread and enforces the review round limit', async () => {
    const { rt, ctx } = await setup(
      [tools(call('complete', { summary: 'v1' })), tools(call('complete', { summary: 'v2' }))],
      { review_rounds: 1 },
    );
    const id = idOf(await spawnThreadTool.execute({ title: 'Draft', brief: 'b' }, ctx));
    await rt.whenIdle();
    expect(getAgent(h.store.db, id)?.status).toBe('done');
    await messageThreadTool.execute({ thread_id: id, kind: 'revision', text: 'Add sources.' }, ctx);
    await rt.whenIdle();
    expect(getAgent(h.store.db, id)).toMatchObject({ status: 'done', review_round: 1, result_summary: 'v2' });
    await expect(messageThreadTool.execute({ thread_id: id, kind: 'revision', text: 'More' }, ctx)).rejects.toThrow(/review round limit/i);
  });
});

describe('approvals, stop and inspection', () => {
  it('resolves only delegated approvals', async () => {
    const delegated = [{ tool: 'bash', match: { command: 'deploy' }, action: 'ask' as const, delegate_to_desk: true }];
    const { rt, projectId, ctx } = await setup([tools(call('bash', { command: 'echo deploy' })), text('deployed'), tools(call('bash', { command: 'sudo ls' }))], {
      policy: [...delegated, { tool: 'bash', match: { command: 'sudo' }, action: 'ask' as const }],
    });
    const a = idOf(await spawnThreadTool.execute({ title: 'A', brief: 'b' }, ctx));
    await rt.whenIdle();
    const [ap] = listApprovals(h.store.db, projectId, 'pending');
    await resolveApprovalTool.execute({ approval_id: ap!.id, decision: 'approve', note: 'ok' }, ctx);
    await rt.whenIdle();
    expect(listApprovals(h.store.db, projectId, 'approved')[0]).toMatchObject({ resolved_by: 'desk' });
    expect(getAgent(h.store.db, a)?.status).toBe('idle');
    idOf(await spawnThreadTool.execute({ title: 'B', brief: 'b' }, ctx));
    await rt.whenIdle();
    const [ap2] = listApprovals(h.store.db, projectId, 'pending');
    await expect(resolveApprovalTool.execute({ approval_id: ap2!.id, decision: 'approve', note: '' }, ctx)).rejects.toThrow(/user/);
  });

  it('stops threads silently and lists/reads them', async () => {
    const { rt, projectId, ctx } = await setup([tools(call('complete', { summary: 'Found pricing' })), tools(call('wait_for_reply', {}))]);
    const done = idOf(await spawnThreadTool.execute({ title: 'Pricing', brief: 'Find pricing' }, ctx));
    await rt.whenIdle();
    const waiting = idOf(await spawnThreadTool.execute({ title: 'Waiter', brief: 'Wait' }, ctx));
    await rt.whenIdle();
    await stopThreadTool.execute({ thread_id: waiting, reason: 'not needed' }, ctx);
    expect(getAgent(h.store.db, waiting)?.status).toBe('cancelled');
    const list = String(await listThreadsTool.execute({}, ctx));
    expect(list).toContain(`${done} "Pricing" [done]`);
    expect(list).toContain(`${waiting} "Waiter" [cancelled]`);
    const summary = String(await readThreadTool.execute({ thread_id: done, mode: 'summary' }, ctx));
    expect(summary).toContain('Status: done');
    expect(summary).toContain('Brief:\n> Find pricing');
    expect(summary).toContain('Result:\n> Found pricing');
    const full = String(await readThreadTool.execute({ thread_id: done, mode: 'full' }, ctx));
    expect(full).toContain('→ complete(');
    expect(listThreads(h.store.db, projectId)).toHaveLength(2);
  });

  it('shows the diff of a git thread', async () => {
    const { rt, projectId, ctx } = await setup([tools(call('write_file', { path: 'b.txt', content: 'new file\n' })), text('wrote it')]);
    const sourceId = await rt.addSource(projectId, await gitRepo());
    const id = idOf(await spawnThreadTool.execute({ title: 'Add b', brief: 'b', git_source_id: sourceId }, ctx));
    await rt.whenIdle();
    expect(String(await reviewDiffTool.execute({ thread_id: id }, ctx))).toContain('+new file');
  });
});

describe('plan, report, questions, settings', () => {
  it('records plan and report, and ask_user yields waiting', async () => {
    const { projectId, ctx } = await setup();
    await updatePlanTool.execute({ items: [{ title: 'Research', status: 'in_progress', thread_ids: [], notes: '' }] }, ctx);
    expect(getPlan(h.store.db, projectId)?.items[0]).toMatchObject({ title: 'Research', status: 'in_progress', id: expect.any(String) });
    await reportTool.execute({ headline: 'Halfway', progress: '1 of 2 done', needs_you: ['Pick a region'], results: [] }, ctx);
    expect(h.store.list({ projectId, types: ['report'] })).toHaveLength(1);
    const out = await askUserTool.execute({ question: 'EU or US?', options: ['EU', 'US'] }, ctx);
    expect(out).toMatchObject({ yield: { status: 'waiting' } });
    expect(h.store.list({ projectId, types: ['question.asked'] })).toHaveLength(1);
  });

  it('validates settings updates', async () => {
    const { projectId, ctx } = await setup();
    await updateSettingsTool.execute({ check_in: 'minimal' }, ctx);
    await expect(updateSettingsTool.execute({ thread_model: 'no-such-model' }, ctx)).rejects.toThrow(/Unknown model/);
    const { getProject } = await import('../state/queries');
    expect(getProject(h.store.db, projectId)?.settings.check_in).toBe('minimal');
  });
});

describe('archiveThread', () => {
  it('removes a finished worktree but keeps its branch', async () => {
    const { rt, projectId, ctx } = await setup([tools(call('complete', { summary: 'done' }))]);
    const repo = await gitRepo();
    const sourceId = await rt.addSource(projectId, repo);
    const id = idOf(await spawnThreadTool.execute({ title: 'Arch', brief: 'b', git_source_id: sourceId }, ctx));
    await rt.whenIdle();
    const t: AgentRow = getAgent(h.store.db, id)!;
    await rt.archiveThread(id);
    expect(existsSync(t.workspace_path!)).toBe(false);
    expect(execFileSync('git', ['branch', '--list', t.git_branch!], { cwd: repo, encoding: 'utf8' })).toContain(t.git_branch!);
    expect(getAgent(h.store.db, id)?.archived_at).toBeTruthy();
  });
});

describe('message_thread refusals', () => {
  it('refuses a note to a finished thread and any message to a stopped or archived one', async () => {
    const { rt, projectId, ctx } = await setup();
    const thread = (title: string, status?: AgentStatus, archived = false) => {
      const id = rt.createThread(projectId, { title, brief: 'b', workspacePath: join(h.dir, title) });
      if (status) h.store.append({ project_id: projectId, agent_id: id, type: 'agent.status_changed', payload: { status } });
      if (archived) h.store.append({ project_id: projectId, agent_id: id, type: 'agent.archived', payload: {} });
      return id;
    };
    const finished = (title: string) =>
      `"${title}" has finished; its result is final. Send kind "question" to ask about its work, "revision" if it fell short of its brief, or spawn a new thread whose brief points at its result or branch.`;
    const send = (thread_id: string, kind: 'note' | 'revision') => messageThreadTool.execute({ thread_id, kind, text: 'More, please.' }, ctx);

    await expect(send(thread('Pricing', 'done'), 'note')).rejects.toThrow(finished('Pricing'));
    await expect(send(thread('Deploy', 'failed'), 'note')).rejects.toThrow(finished('Deploy'));
    const stopped = thread('Scout', 'cancelled');
    await expect(send(stopped, 'note')).rejects.toThrow('"Scout" was stopped; it cannot receive messages.');
    await expect(send(stopped, 'revision')).rejects.toThrow('"Scout" was stopped; it cannot receive messages.');
    await expect(send(thread('Old', 'done', true), 'revision')).rejects.toThrow('"Old" is archived.');
    expect(h.store.list({ projectId, types: ['message.agent', 'agent.revision'] })).toEqual([]);

    const fresh = thread('Fresh');
    await expect(send(fresh, 'note')).resolves.toMatch(/^Sent note #\d+ to "Fresh" \(idle: it is woken to read this\)\.$/);
    await rt.whenIdle();
  });
});

describe('thread references', () => {
  it('take an id or an exact title, prefer a live thread, and refuse archived and ambiguous ones', async () => {
    const { rt, projectId, ctx } = await setup();
    let n = 0;
    const make = (title: string) => rt.createThread(projectId, { title, brief: `Find ${title}`, workspacePath: join(h.dir, `ref-${++n}`) });
    const old = make('Pricing');
    h.store.append({ project_id: projectId, agent_id: old, type: 'agent.status_changed', payload: { status: 'done' } });
    h.store.append({ project_id: projectId, agent_id: old, type: 'agent.archived', payload: {} });
    const live = make('Pricing');
    const read = async (thread_id: string) => String(await readThreadTool.execute({ thread_id, mode: 'summary' }, ctx));

    expect(await read('Pricing')).toContain(`Thread ${live} "Pricing"`);
    expect(await read(live)).toContain(`Thread ${live} "Pricing"`);
    await expect(read(old)).rejects.toThrow('"Pricing" is archived.');
    await expect(read('pricing')).rejects.toThrow('Unknown thread: pricing');
    make('Twin');
    make('Twin');
    await expect(read('Twin')).rejects.toThrow('Several threads are titled "Twin"; use its id.');
    const archivedAuth = make('Auth API');
    h.store.append({ project_id: projectId, agent_id: archivedAuth, type: 'agent.status_changed', payload: { status: 'done' } });
    h.store.append({ project_id: projectId, agent_id: archivedAuth, type: 'agent.archived', payload: {} });
    await expect(read('Auth API')).rejects.toThrow('"Auth API" is archived.');
    // A live thread a header names "Auth API" wins over the archived one titled so.
    const bracketed = make('Auth] API');
    expect(await read('Auth API')).toContain(`Thread ${bracketed} `);

    await expect(stopThreadTool.execute({ thread_id: 'Pricing', reason: 'Not needed' }, ctx)).resolves.toBe('Stopped Pricing.');
    expect(getAgent(h.store.db, live)?.status).toBe('cancelled');
    expect(getAgent(h.store.db, old)?.status).toBe('done');
  });
});

describe('message_thread through the send path', () => {
  it('asks a thread by title, tracked, and activates skills only with a message it sent', async () => {
    const { rt, projectId, ctx } = await setup();
    rt.saveSkill({ scope: 'project', projectId, name: 'house-style', description: 'Our writing style', instructions: 'STYLE: short sentences.' });
    const t = rt.createThread(projectId, { title: 'Pricing', brief: 'Find pricing', workspacePath: join(h.dir, 'pricing') });
    // Marked running without a job: what it is sent waits for a next step that never comes.
    h.store.append({ project_id: projectId, agent_id: t, type: 'agent.status_changed', payload: { status: 'running' } });
    const sent = () => h.store.list({ agentId: t, types: ['message.agent'] }) as Array<EventOf<'message.agent'>>;

    await expect(messageThreadTool.execute({ thread_id: 'Pricing', kind: 'note', text: 'Use it.', skills: ['no-such-skill'] }, ctx)).rejects.toThrow(
      'Unknown skill: no-such-skill',
    );
    await expect(messageThreadTool.execute({ thread_id: 'Nobody', kind: 'note', text: 'Use it.', skills: ['house-style'] }, ctx)).rejects.toThrow(
      'Unknown thread: Nobody',
    );
    expect(sent()).toEqual([]);
    expect(getAgent(h.store.db, t)!.active_skills).toEqual([]);

    const out = await messageThreadTool.execute({ thread_id: 'Pricing', kind: 'question', text: 'Per seat?', skills: ['house-style'] }, ctx);
    const [q] = sent();
    expect(out).toBe(`Sent question #${q!.id} to "Pricing" (running: it sees this at its next step).`);
    expect(q!.payload).toMatchObject({ from_label: 'Desk', kind: 'question', text: 'Per seat?', tracked: true, tool_call_id: 'tc' });
    expect(getAgent(h.store.db, t)!.active_skills).toEqual(['house-style']);
    expect(messageThreadTool.description).toBe(
      'Send a message to a thread. `note`: context, a redirection or follow-up work for a thread that is still working; your notes carry your authority. `question`: ask it something only it knows (for status or results use read_thread instead); a finished thread is woken just to answer, from its context (a model run), and its result stays final. `revision`: send finished work back with specific feedback; reopens it, limited by the project review-round setting. If the thread asked you a question, your next message to it is recorded as the answer. Pass skills to activate more skills on it. At most 4000 characters.',
    );
  });
});
