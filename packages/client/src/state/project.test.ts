import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import type { ProjectOverview } from '../types';
import { projectFromOverview, reduceProject } from './project';

const overview: ProjectOverview = {
  project: { id: 'p', name: 'Onboarding', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, desk_reasoning_effort: null, thread_reasoning_effort: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'm', reasoning_effort: null, title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: null,
  threads: [],
  approvals: [],
  last_seq: 2,
};

const fold = (events: Parameters<typeof reduceProject>[1][]) => events.reduce(reduceProject, projectFromOverview(overview));

describe('reduceProject', () => {
  it("takes What's up from the overview, then from Desk's updates", () => {
    expect(fold([]).whatsUp).toBeNull();
    const s = [ev(3, 'whats_up.updated', { text: 'Two threads on the relaunch.' }, { agent: 'd', ts: '2026-09-25T10:00:00Z' })].reduce(
      reduceProject,
      projectFromOverview({ ...overview, whats_up: { text: 'Scoping.', ts: '2026-09-25T09:00:00Z' } }),
    );
    expect(s.whatsUp).toEqual({ text: 'Two threads on the relaunch.', ts: '2026-09-25T10:00:00Z' });
  });

  it('tracks threads from creation through activity, approval, revision, fallback and archive', () => {
    const s = fold([
      ev(3, 'agent.created', { role: 'thread', model: 'm', reasoning_effort: 'high', title: 'Emails', brief: 'b', workspace_path: '/w/t', parent_id: 'd', skills: ['brand-voice'] }, { agent: 't' }),
      ev(4, 'agent.status_changed', { status: 'running' }, { agent: 't' }),
      ev(5, 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'write_file', arguments: '{"path":"emails/04.md","content":"…"}' }, { agent: 't' }),
      ev(6, 'approval.requested', { approval_id: 'a1', run_id: 'r', tool_call_id: 'c2', tool: 'bash', arguments: '{}', reason: 'rule 1', delegate_to_desk: false }, { agent: 't' }),
      ev(7, 'agent.status_changed', { status: 'waiting', reason: 'Waiting for approval' }, { agent: 't' }),
      ev(8, 'agent.model_switched', { from: 'm', to: 'fallback', reason: 'rate limited', scope: 'run' }, { agent: 't' }),
    ]);
    expect(s.threads).toHaveLength(1);
    expect(s.threads[0]).toMatchObject({ id: 't', status: 'waiting', reason: 'Waiting for approval', activity: 'write_file · emails/04.md', active_skills: ['brand-voice'], model_override: 'fallback', reasoning_effort: 'high', effort: 'high' });
    expect(s.approvals.map((a) => a.id)).toEqual(['a1']);
    expect(s.lastSeq).toBe(8);

    const s2 = [
      ev(9, 'approval.resolved', { approval_id: 'a1', decision: 'approved', resolved_by: 'user' }, { agent: 't' }),
      ev(10, 'tool.result', { run_id: 'r', tool_call_id: 'c', name: 'write_file', status: 'ok', content: 'ok' }, { agent: 't' }),
      ev(11, 'agent.result', { summary: 'Five drafts', artifacts: ['emails/01.md'] }, { agent: 't' }),
      ev(12, 'agent.status_changed', { status: 'done' }, { agent: 't' }),
      ev(13, 'agent.revision', { round: 1, feedback: 'Less salesy' }, { agent: 't' }),
      ev(14, 'run.started', { run_id: 'r2', model: 'm', reasoning_effort: 'xhigh' }, { agent: 't' }),
      ev(15, 'agent.archived', {}, { agent: 't' }),
    ].reduce(reduceProject, s);
    expect(s2.approvals).toEqual([]);
    expect(s2.threads[0]).toMatchObject({ activity: null, result_summary: 'Five drafts', review_round: 1, model_override: null, effort: 'xhigh', reason: null, status: 'done' });
    expect(s2.threads[0]!.archived_at).not.toBeNull();
  });

  it('updates project fields, settings, sources, plan and desk; ignores replayed events', () => {
    const s = fold([
      ev(3, 'project.updated', { goal: 'New goal', settings: { check_in: 'minimal' } }),
      ev(4, 'source.added', { source_id: 's1', path: '/repo', kind: 'git', label: 'repo' }),
      ev(5, 'plan.updated', { items: [{ id: '1', title: 'A', status: 'todo', thread_ids: [], notes: '' }] }),
      ev(6, 'agent.status_changed', { status: 'running' }, { agent: 'd' }),
      ev(7, 'source.removed', { source_id: 's1' }),
    ]);
    expect(s.project.goal).toBe('New goal');
    expect(s.project.settings.check_in).toBe('minimal');
    expect(s.project.settings.review_rounds).toBe(2);
    expect(s.sources).toEqual([]);
    expect(s.plan.map((i) => i.title)).toEqual(['A']);
    expect(s.desk?.status).toBe('running');
    expect(reduceProject(s, ev(2, 'project.updated', { goal: 'stale' }))).toBe(s);
  });

  it('tracks services through start, URL, exit, restart and stop, sorted by name', () => {
    const start = (id: number, sid: string, name: string, by = 'user') =>
      ev(id, 'service.started', { service_id: sid, name, command: 'npm run dev', cwd: '.', workspace_agent_id: 't', pid: 100 + id, by });
    const s = fold([
      start(3, 's1', 'web'),
      ev(4, 'service.url', { service_id: 's1', url: 'http://localhost:5173' }),
      start(5, 's2', 'api', 'agent:d'),
      ev(6, 'service.exited', { service_id: 's2', code: 1, signal: null }),
    ]);
    expect(s.services.map((x) => [x.name, x.status, x.url, x.exit_code])).toEqual([
      ['api', 'exited', null, 1],
      ['web', 'running', 'http://localhost:5173', null],
    ]);
    const s2 = [start(7, 's2', 'api'), ev(8, 'service.stopped', { service_id: 's1', by: 'user', reason: 'requested' })].reduce(reduceProject, s);
    expect(s2.services.find((x) => x.name === 'api')).toMatchObject({ status: 'running', exit_code: null, pid: 107, started_by: 'user' });
    expect(s2.services.find((x) => x.name === 'web')).toMatchObject({ status: 'stopped', stop_reason: 'requested', url: 'http://localhost:5173' });
  });
});
