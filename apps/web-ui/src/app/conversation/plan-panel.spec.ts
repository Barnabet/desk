import { render, screen, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { ProjectState, ThreadView } from '@desk/client';
import type { AttentionItem, PlanItem } from '@desk/protocol';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { PlanPanel } from './plan-panel';

const thread = (id: string, title: string | null, status: ThreadView['status'], extra: Record<string, unknown> = {}) =>
  ({ id, title, status, reason: null, archived_at: null, git_branch: null, review_round: 0, model: 'm', model_override: null, ...extra }) as unknown as ThreadView;
const step = (id: string, title: string, status: PlanItem['status'], threadIds: string[] = [], notes = ''): PlanItem => ({ id, title, status, thread_ids: threadIds, notes });
const project = (o: { plan: PlanItem[]; threads: ThreadView[]; desk?: ThreadView | null; autonomy?: string }) =>
  ({
    project: { id: 'p', name: 'Onboarding revamp', settings: { review_rounds: 2, check_in: 'normal', autonomy: o.autonomy ?? 'dispatch-freely' } },
    desk: o.desk ?? null,
    sources: [],
    plan: o.plan,
    threads: o.threads,
    approvals: [],
    services: [],
    lastSeq: 0,
  }) as unknown as ProjectState;
const item = (id: string, kind: AttentionItem['kind'], agent: string): AttentionItem => ({
  id,
  kind,
  project_id: 'p',
  project_name: 'Onboarding revamp',
  agent_id: agent,
  title: id,
  detail: '',
  created_at: '2026-09-25T10:00:00.000Z',
  ref: { thread_id: agent },
});

async function show(p: ProjectState, attention: AttentionItem[] = [], proxyDown = false) {
  await render(`<aside deskPlanPanel [project]="project" [proxyDown]="proxyDown" [attention]="attention"></aside>`, {
    imports: [PlanPanel],
    componentProperties: { project: p, attention, proxyDown },
    providers: new FakeDeskBridge().providers,
  });
  return screen.getByRole('complementary', { name: 'Plan and Desk' });
}

describe('PlanPanel', () => {
  it('draws the plan as a route: a tone per stop, its threads with their revision, and "waiting on you" only when an item holds the thread', async () => {
    const panel = await show(
      project({
        plan: [
          step('1', 'Research', 'done', ['r']),
          step('2', 'Login API', 'in_progress', ['a']),
          step('3', 'Login page', 'in_progress', ['f']),
          step('4', 'Billing', 'in_progress', ['b'], 'Needs the tax table'),
          step('5', 'Launch post', 'todo'),
          step('6', 'Old idea', 'dropped'),
        ],
        threads: [thread('r', 'Research', 'done'), thread('a', 'Auth API', 'running', { review_round: 1 }), thread('f', 'Frontend', 'waiting'), thread('b', null, 'waiting')],
      }),
      // An approval holds Frontend; a stall asks the user to look, but Billing still waits on its peers.
      [item('approval:x1', 'approval', 'f'), item('stalled:b:9', 'stalled', 'b')],
    );
    expect(within(panel).getByRole('heading', { name: 'Plan' })).toBeTruthy();
    expect(within(panel).getByText('1 of 6 done').className).toBe('chip chip-idle');
    const stops = [...panel.querySelectorAll('.plan-stop')];
    expect(stops.map((s) => s.className)).toEqual(['plan-stop plan-done', 'plan-stop plan-run', 'plan-stop plan-wait', 'plan-stop plan-wait', 'plan-stop plan-todo', 'plan-stop plan-dropped']);
    expect(stops.map((s) => s.querySelector('.plan-sub')!.textContent)).toEqual([
      'Done · Research',
      'In progress · Auth API · revision 1/2',
      'In progress · Frontend · waiting on you',
      'In progress · Thread',
      'To do · Desk, once the threads report',
      'Dropped',
    ]);
    expect(stops.map((s) => s.querySelector('.plan-sub')!.className)).toEqual([
      'plan-sub plan-sub-done',
      'plan-sub plan-sub-run',
      'plan-sub plan-sub-wait',
      'plan-sub plan-sub-wait',
      'plan-sub plan-sub-todo',
      'plan-sub plan-sub-dropped',
    ]);
    expect(within(stops[1]! as HTMLElement).getByRole('link', { name: 'Auth API' }).getAttribute('href')).toBe('#/p/p/threads/a');
    expect(stops[3]!.querySelector('.plan-text > .muted.small')!.textContent).toBe('Needs the tax table');
  });

  it("says Desk writes the plan, lists the branches Desk leaves you to merge, and shows Desk's card", async () => {
    const desk = thread('d', 'Desk', 'running', { model: 'claude-opus-5-5', model_override: 'claude-sonnet-5' });
    const panel = await show(
      project({
        plan: [],
        threads: [thread('a', 'Auth API', 'running', { git_branch: 'desk/auth-api' }), thread('o', 'Old', 'done', { git_branch: 'desk/old', archived_at: '2026-09-24T10:00:00.000Z' })],
        desk,
        autonomy: 'ask-first',
      }),
      [],
      true,
    );
    expect(within(panel).getByText('No plan yet')).toBeTruthy();
    expect(within(panel).getByText('Desk writes the plan once it understands the brief.').className).toBe('muted');
    expect(panel.querySelector('.merge-note > span')!.textContent).toBe("Desk never merges; you'll get a merge order.");
    expect([...panel.querySelectorAll('.merge-note .mono')].map((b) => b.textContent)).toEqual(['desk/auth-api']);
    const card = panel.querySelector('.desk-card')!;
    expect(card.querySelector('.desk-card-head')!.textContent).toBe('DeskDesk coordinatorPaused, will resume');
    expect([...card.querySelectorAll('dl > div')].map((d) => d.textContent)).toEqual(['Modelclaude-sonnet-5', 'Check-insnormal', 'Autonomyasks before dispatching']);
  });
});
