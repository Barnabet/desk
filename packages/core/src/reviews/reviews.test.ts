import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { getAgent, getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { chooseReviewModel, currentReviewOf, findingsOfBuilder, latestSubmission, listSubmissions } from './reviews';
import { resolveSettings } from '@desk/protocol';

let h: Harness;
let rt: Runtime;
afterEach(async () => h?.cleanup());

const systemOf = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
/** How many times the conversation already called `name`. */
const callsOf = (req: ChatRequest, name: string) =>
  req.messages
    .filter((m) => m.role === 'assistant')
    .flatMap((m) => m.tool_calls ?? [])
    .filter((c) => c.function?.name === name).length;
const lastText = (req: ChatRequest) => String(req.messages.at(-1)?.content ?? '');
const git = (cwd: string, ...args: string[]) => execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd, encoding: 'utf8' }).trim();

/**
 * A builder ("Comparator") that edits README.md, tries to complete before committing, commits, completes; on a
 * revision it fixes and completes again. Its reviewer reads the builder, raises a blocking finding, files an initial
 * then a final assessment; on a re-review it marks the finding fixed and finds no material issues.
 */
function script(ids: { builder?: string }) {
  const builder = (req: ChatRequest): FakeReply => {
    const [writes, completes, commits] = [callsOf(req, 'write_file'), callsOf(req, 'complete'), callsOf(req, 'git_commit')];
    if (writes === 0) return tools(call('write_file', { path: 'README.md', content: 'v1\n' }));
    if (writes === 1 && completes === 0) return tools(call('complete', { summary: 'Comparator done' }));
    if (completes === 1 && commits === 0) return tools(call('git_commit', { message: 'v1' }));
    if (commits === 1 && completes === 1)
      return tools(call('complete', { summary: 'Comparator done', claims: ['Keeps duplicate fact IDs'], limitations: ['Not tested on BPCE'], evidence: 'Ran the unit tests' }));
    if (completes === 2 && writes === 1) return tools(call('write_file', { path: 'README.md', content: 'v2\n' }));
    if (writes === 2 && commits === 1) return tools(call('git_commit', { message: 'Keep duplicates' }));
    if (commits === 2 && completes === 2) return tools(call('complete', { summary: 'Fixed duplicates', claims: ['Keeps duplicate fact IDs'] }));
    return text('done');
  };
  const reviewer = (req: ChatRequest): FakeReply => {
    const [reads, raised, assessed, resolved] = [callsOf(req, 'read_thread'), callsOf(req, 'raise_finding'), callsOf(req, 'submit_assessment'), callsOf(req, 'resolve_finding')];
    const reqs = (met: 'yes' | 'no') => [{ criterion: 'Duplicate fact IDs are kept', met, note: met === 'no' ? 'one is dropped' : 'checked' }];
    if (reads === 0) return tools(call('read_thread', { thread_id: ids.builder }));
    if (raised === 0) return tools(call('raise_finding', { title: 'Drops a duplicate fact ID', detail: 'The comparator discards it.', blocking: true, reproducer: 'diff a.json b.json' }));
    if (assessed === 0) return tools(call('submit_assessment', { verdict: 'issues_found', requirements: reqs('no'), not_checked: ['Performance'] }));
    if (assessed === 1) return tools(call('submit_assessment', { verdict: 'issues_found', requirements: reqs('no'), not_checked: ['Performance'] }));
    if (assessed === 2 && resolved === 0) {
      const finding = /\b(f_[\w-]+)/.exec(lastText(req))?.[1] ?? 'missing';
      return tools(call('resolve_finding', { finding_id: finding, outcome: 'fixed', note: 'README now keeps it' }));
    }
    if (assessed === 2 && resolved === 1) return tools(call('submit_assessment', { verdict: 'no_material_issues', requirements: reqs('yes') }));
    return text('done');
  };
  return (req: ChatRequest): FakeReply => {
    const s = systemOf(req);
    if (s.startsWith('You are Desk')) return text('noted');
    if (s.includes('## Your assignment: Review: Comparator\n')) return reviewer(req);
    if (s.includes('## Your assignment: Comparator\n')) return builder(req);
    return text('ok');
  };
}

async function setup() {
  const ids: { builder?: string } = {};
  h = await createHarness({ script: script(ids) });
  rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id, review_model: FAKE_MODEL.id } });
  const repo = join(h.files, 'repo');
  mkdirSync(repo);
  git(repo, 'init', '-q');
  writeFileSync(join(repo, 'README.md'), 'v0\n');
  git(repo, 'add', '-A');
  git(repo, 'commit', '-qm', 'init');
  const sourceId = await rt.addSource(projectId, repo, 'repo');
  const desk = getDeskAgent(h.store.db, projectId)!;
  const builder = await rt.spawnThread(desk.id, { title: 'Comparator', brief: 'Make the comparator keep duplicate fact IDs.', gitSourceId: sourceId });
  ids.builder = builder;
  await rt.whenIdle();
  return { projectId, desk, builder, repo };
}

describe('reviews and acceptance', () => {
  it('pins a submission to a commit, refusing uncommitted work', async () => {
    const { builder } = await setup();
    const b = getAgent(h.store.db, builder)!;
    expect(b).toMatchObject({ status: 'done', acceptance: 'none', result_summary: 'Comparator done' });
    const refused = h.store.list({ agentId: builder, types: ['tool.result'] }).find((e) => e.type === 'tool.result' && e.payload.name === 'complete' && e.payload.status === 'error');
    expect(refused?.type === 'tool.result' && refused.payload.content).toMatch(/Commit your work first/);
    const [sub] = listSubmissions(h.store.db, builder);
    expect(sub).toMatchObject({ seq: 1, commit: git(b.workspace_path!, 'rev-parse', 'HEAD'), base: b.git_base, claims: ['Keeps duplicate fact IDs'], limitations: ['Not tested on BPCE'], evidence: 'Ran the unit tests' });
    const notice = h.store.list({ agentId: getDeskAgent(h.store.db, b.project_id)!.id, types: ['message.agent'] }).at(-1);
    expect(notice?.type === 'message.agent' && notice.payload.text).toContain(`Submission 1 (${sub!.id}): commit ${sub!.commit!.slice(0, 10)}, 0 artifacts.`);
  });

  it('reviews independently, records findings, and accepts only against the reviewed version', async () => {
    const { builder, desk } = await setup();
    const sub1 = latestSubmission(h.store.db, builder)!;
    await expect(rt.requestReview({ threadId: builder, criteria: [' '], by: 'user' })).rejects.toThrow('Give at least one acceptance criterion');

    const { reviewerId } = await rt.requestReview({ threadId: builder, criteria: ['Duplicate fact IDs are kept'], focus: 'duplicates', by: 'desk' });
    const reviewer = getAgent(h.store.db, reviewerId)!;
    expect(reviewer).toMatchObject({ title: 'Review: Comparator', reviews_submission_id: sub1.id, parent_id: desk.id });
    expect(reviewer.git_branch).toMatch(/^desk\/review-comparator-/);
    expect(git(reviewer.workspace_path!, 'rev-parse', 'HEAD')).toBe(sub1.commit);
    expect(getAgent(h.store.db, builder)!.acceptance).toBe('in_review');
    // Sealed until the reviewer's initial assessment, both ways.
    expect(() => rt.send({ from: builder, to: reviewerId, kind: 'note', text: 'It is correct.' })).toThrow(/reviewing your work independently/);
    expect(() => rt.send({ from: reviewerId, to: builder, kind: 'question', text: 'Why?' })).toThrow(/before your initial assessment/);
    await expect(rt.requestReview({ threadId: builder, criteria: ['x'], by: 'desk' })).rejects.toThrow(/already being reviewed/);
    await rt.whenIdle();

    const results = h.store.list({ agentId: reviewerId, types: ['tool.result'] }).flatMap((e) => (e.type === 'tool.result' ? [e.payload] : []));
    const read = results.find((r) => r.name === 'read_thread')!;
    expect(read.content).toContain('withheld until your initial assessment');
    expect(read.content).not.toContain('Comparator done');
    const initial = results.find((r) => r.name === 'submit_assessment')!;
    expect(initial.content).toContain('Initial assessment recorded.');
    expect(initial.content).toContain('> Comparator done');
    expect(initial.content).toContain('> - Not tested on BPCE');
    expect(getAgent(h.store.db, reviewerId)).toMatchObject({ status: 'done' });
    expect(getAgent(h.store.db, reviewerId)!.result_summary).toMatch(/^Review of "Comparator": issues found, 1 open finding \(1 blocking\)\./);
    expect(currentReviewOf(h.store.db, reviewerId)).toMatchObject({ phase: 'final', verdict: 'issues_found', not_checked: ['Performance'] });
    expect(getAgent(h.store.db, builder)!.acceptance).toBe('reviewed');
    const [finding] = findingsOfBuilder(h.store.db, builder);
    expect(finding).toMatchObject({ blocking: true, state: 'open', submission_id: sub1.id });

    // A blocking finding stops acceptance; Desk must give reasons to waive.
    expect(() => rt.acceptSubmission({ threadId: builder, decision: 'accepted', by: 'desk' })).toThrow(/open blocking findings/);
    expect(() => rt.acceptSubmission({ threadId: builder, decision: 'accepted', waive: [{ findingId: finding!.id }], by: 'desk' })).toThrow(/Give a reason/);
    expect(() => rt.acceptSubmission({ threadId: builder, decision: 'accepted_with_limitations', by: 'desk' })).toThrow(/which limitations/);

    // Changes requested: a revision with the open finding; the builder fixes and submits again.
    rt.acceptSubmission({ threadId: builder, decision: 'changes_requested', note: 'Keep every duplicate.', by: 'desk' });
    expect(getAgent(h.store.db, builder)).toMatchObject({ acceptance: 'changes_requested', review_round: 1 });
    await rt.whenIdle();
    const sub2 = latestSubmission(h.store.db, builder)!;
    expect(sub2.seq).toBe(2);
    expect(listSubmissions(h.store.db, builder)[0]!.superseded_by).toBe(sub2.id);
    expect(getAgent(h.store.db, builder)!.acceptance).toBe('none');

    // The same reviewer re-checks the new commit and marks the finding fixed.
    const again = await rt.requestReview({ threadId: builder, criteria: ['Duplicate fact IDs are kept'], reviewerId, by: 'desk' });
    expect(again).toMatchObject({ reviewerId, reopened: true });
    expect(git(reviewer.workspace_path!, 'rev-parse', 'HEAD')).toBe(sub2.commit);
    await rt.whenIdle();
    expect(findingsOfBuilder(h.store.db, builder)[0]).toMatchObject({ state: 'fixed', fixed_in: sub2.id, resolved_by: 'reviewer' });
    expect(currentReviewOf(h.store.db, reviewerId)).toMatchObject({ phase: 'final', verdict: 'no_material_issues', submission_id: sub2.id });
    expect(getAgent(h.store.db, builder)!.acceptance).toBe('reviewed');

    rt.acceptSubmission({ threadId: builder, decision: 'accepted', by: 'user' });
    expect(getAgent(h.store.db, builder)).toMatchObject({ acceptance: 'accepted', accepted_submission_id: sub2.id });
    const told = h.store.list({ agentId: desk.id, types: ['message.agent'] }).filter((e) => e.type === 'message.agent' && e.payload.kind === 'reminder');
    const last = told.at(-1);
    expect(last?.type === 'message.agent' ? last.payload.text : undefined).toBe('The user accepted submission 2 of "Comparator".');
    expect(() => rt.acceptSubmission({ threadId: reviewerId, decision: 'accepted', by: 'desk' })).toThrow(/is a reviewer/);
  });
});

describe('chooseReviewModel', () => {
  const models = new Map([
    ['claude-a', { family: 'claude' }],
    ['claude-b', { family: 'claude' }],
    ['gpt-a', { family: 'gpt' }],
  ]);
  const registry = {
    get: (id: string) => {
      const m = models.get(id);
      if (!m) throw new Error('unknown');
      return { id, ...m } as never;
    },
    list: () => [...models].map(([id, m]) => ({ id, ...m })) as never[],
  };
  it('prefers the request, then the setting, then another family', () => {
    const s = resolveSettings({ desk_model: 'claude-a', thread_model: 'claude-b' });
    expect(chooseReviewModel(s, 'claude-b', registry, 'claude-a')).toBe('claude-a');
    expect(chooseReviewModel({ ...s, review_model: 'claude-b' }, 'claude-b', registry)).toBe('claude-b');
    expect(chooseReviewModel(s, 'claude-b', registry)).toBe('gpt-a');
    expect(chooseReviewModel({ ...s, desk_model: 'gpt-a' }, 'claude-b', registry)).toBe('gpt-a');
    expect(chooseReviewModel(s, 'gpt-a', registry)).toBe('claude-a');
  });
});
