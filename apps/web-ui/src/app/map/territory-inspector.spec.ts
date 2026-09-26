import { render, screen, waitFor } from '@testing-library/angular';
import type { AttentionItem, OverviewThread, PlanItem, ProjectSummary } from '@desk/protocol';
import { describe, expect, it } from 'vitest';
import { DeskBridge } from '../core/desk-bridge';
import { FakeDeskBridge, type FakeHandlers } from '../testing/fake-bridge';
import { TerritoryInspector } from './territory-inspector';

type Plan = { project_id: string; items: PlanItem[]; updated_at: string };

const NOW = Date.parse('2026-09-24T11:26:00.000Z');
const thread = (id: string, title: string | null, status: OverviewThread['status'], extra: Partial<OverviewThread> = {}): OverviewThread => ({ id, title, status, reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '2026-09-24T11:21:00.000Z', updated_at: '2026-09-24T11:21:00.000Z', ...extra });
const project = (id: string, name: string, threads: OverviewThread[], attention = 0): ProjectSummary => ({
  project: { id, name, goal: `${name} goal`, updated_at: '2026-09-24T10:00:00.000Z' },
  desk_status: 'idle',
  threads,
  latest_report: { headline: `${name}: research is in`, ts: '2026-09-24T11:24:00.000Z' },
  plan_progress: { done: 1, total: 3 },
  attention_count: attention,
});
const step = (id: string, status: PlanItem['status'], threadIds: string[] = []): PlanItem => ({ id, title: `Step ${id}`, status, thread_ids: threadIds, notes: '' });

const onboarding = project(
  'p1',
  'Onboarding revamp',
  [
    thread('t1', 'Funnel analysis', 'running'),
    thread('t2', 'Signup checklist', 'waiting'),
    thread('t3', 'Pricing page', 'waiting'),
    thread('t4', 'Copy review', 'running', { review_round: 2 }),
    thread('t5', 'Receipts', 'queued', { reason: 'Desk restarted' }),
    thread('t6', 'Invoices', 'queued'),
    thread('t7', 'Archive', 'done'),
    thread('t8', null, 'failed'),
  ],
  2,
);
const approval: AttentionItem = { id: 'approval:a1', kind: 'approval', project_id: 'p1', project_name: 'Onboarding revamp', agent_id: 't2', title: 'Signup checklist wants to run bash', detail: 'rule 1', created_at: '2026-09-24T11:21:00.000Z', ref: { approval_id: 'a1', thread_id: 't2' } };
const stalled: AttentionItem = { id: 'stalled:t3:12', kind: 'stalled', project_id: 'p1', project_name: 'Onboarding revamp', agent_id: 't3', title: 'Pricing page has gone quiet', detail: '', created_at: '2026-09-24T11:20:00.000Z', ref: { thread_id: 't3', event_id: 12 } };

async function setup(o: { p?: ProjectSummary; items?: AttentionItem[]; plan?: FakeHandlers['projects.plan'] } = {}) {
  const bridge = new FakeDeskBridge({ 'projects.plan': o.plan ?? (() => null) });
  const view = await render(TerritoryInspector, {
    inputs: { p: o.p ?? onboarding, items: o.items ?? [approval, stalled], now: NOW },
    providers: [{ provide: DeskBridge, useValue: bridge }],
    // The host must be the <article> its selector names (TestBed uses a <div> otherwise): the first case checks its tag.
    configureTestBed: (testBed) => testBed.configureTestingModule({ inferTagName: true }),
  });
  const planCalls = () => bridge.calls.filter((c) => c.channel === 'projects.plan').map((c) => c.input);
  return { bridge, view, planCalls };
}

describe('TerritoryInspector', () => {
  it('names the project under its tone chip and quotes the latest report', async () => {
    await setup();
    const card = screen.getByRole('region', { name: 'Territory' });
    expect(card.tagName).toBe('ARTICLE');
    expect(card.className).toBe('card territory');
    expect(card.getAttribute('aria-live')).toBe('polite');
    const chip = card.querySelector('.territory-head .chip')!;
    expect(chip.className).toBe('chip chip-run');
    expect(chip.textContent).toBe('2 running · 4 waiting · 1 done · 1 failed');
    expect(screen.getByRole('heading', { level: 2, name: 'Onboarding revamp' }).className).toBe('territory-name');
    expect(card.querySelector('.territory-quote p')!.textContent).toBe('“Onboarding revamp: research is in”');
    expect(card.querySelector('.territory-quote span')!.textContent).toMatch(/^Desk · report \d\d:\d\d$/);
  });

  it('shows the goal before the first report, and says so when there is no goal either', async () => {
    const { view } = await setup({ p: { ...onboarding, latest_report: null } });
    expect(document.querySelector('.territory-quote')).toBeNull();
    expect(screen.getByText('Onboarding revamp goal').className).toBe('muted');
    await view.rerender({ inputs: { p: { ...onboarding, latest_report: null, project: { ...onboarding.project, goal: '' } }, items: [approval, stalled], now: NOW }, partialUpdate: true });
    expect(await screen.findByText('No report yet.')).toBeTruthy();
  });

  it("says each thread's state, and waiting on you only when its item holds it", async () => {
    await setup();
    const rows = Array.from(document.querySelectorAll('.territory-thread'), (a) => [a.querySelector('.grow')!.textContent, a.querySelector('.territory-thread-status')!.textContent]);
    expect(rows).toEqual([
      ['Funnel analysis', 'running · 5m'],
      ['Signup checklist', 'waiting on you'],
      // A stall asks the user to look, but the thread still waits on the agent it asked.
      ['Pricing page', 'waiting'],
      ['Copy review', 'revision 2 · 5m'],
      ['Receipts', 'will resume'],
      ['Invoices', 'queued'],
      ['Archive', 'done'],
      ['Thread', 'failed'],
    ]);
    // The thread row's name runs its spans together ("Pricing pagewaiting"), and the needs box has a "Pricing page…" link too.
    const pricing = document.querySelectorAll<HTMLAnchorElement>('a.territory-thread')[2]!;
    expect(pricing.getAttribute('href')).toBe('#/p/p1/threads/t3');
    expect(pricing.querySelector('.status-dot')!.className).toBe('status-dot status-dot-waiting');
    expect(pricing.querySelector('.territory-thread-status')!.className).toBe('territory-thread-status status-text-waiting');
  });

  it('lists what needs you and opens the first in Attention', async () => {
    await setup();
    expect(screen.getByText('Needs you · 2')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Signup checklist wants to run bash' }).getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    expect(screen.getByRole('link', { name: 'Pricing page has gone quiet' }).getAttribute('href')).toBe('#/attention?item=stalled%3At3%3A12');
    expect(screen.getByRole('link', { name: 'Attention' }).getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    const open = screen.getByRole('link', { name: 'Open conversation' });
    expect(open.getAttribute('href')).toBe('#/p/p1/conversation');
    expect(open.className).toBe('btn btn-primary grow');
  });

  it('has no needs box without items, and opens the whole Attention list', async () => {
    await setup({ items: [] });
    expect(document.querySelector('.needs-box')).toBeNull();
    expect(screen.getByRole('link', { name: 'Attention' }).getAttribute('href')).toBe('#/attention');
  });

  it('loads the plan and draws a waypoint per item, amber where its thread waits', async () => {
    const { planCalls } = await setup({
      plan: ({ id }) => ({ project_id: id, items: [step('1', 'done'), step('2', 'in_progress', ['t1']), step('3', 'in_progress', ['t2']), step('4', 'todo'), step('5', 'dropped')], updated_at: '' }),
    });
    await waitFor(() => expect(document.querySelectorAll('.waypoint')).toHaveLength(5));
    expect(Array.from(document.querySelectorAll('.waypoint'), (w) => w.className)).toEqual(['waypoint waypoint-done', 'waypoint waypoint-run', 'waypoint waypoint-wait', 'waypoint waypoint-todo', 'waypoint waypoint-dropped']);
    expect(document.querySelector('.waypoint')!.getAttribute('title')).toBe('Step 1');
    expect(document.querySelector('.waypoints')!.getAttribute('aria-hidden')).toBe('true');
    expect(screen.getByText('1 of 5 done')).toBeTruthy();
    expect(planCalls()).toEqual([{ id: 'p1' }]);
  });

  it('says when every step is done, and when there is no plan', async () => {
    const { view } = await setup({ plan: ({ id }) => (id === 'p1' ? { project_id: id, items: [step('1', 'done'), step('2', 'done')], updated_at: '' } : null) });
    expect(await screen.findByText('all 2 done')).toBeTruthy();
    await view.rerender({ inputs: { p: { ...project('p2', 'Tax paperwork', []), plan_progress: { done: 0, total: 0 } }, items: [], now: NOW }, partialUpdate: true });
    expect(await screen.findByText('no plan yet')).toBeTruthy();
    expect(document.querySelector('.waypoints')).toBeNull();
  });

  it('shows no plan when it cannot be read', async () => {
    await setup({
      plan: () => {
        throw { code: 'daemon_unavailable', message: 'deskd is not running' };
      },
    });
    expect(await screen.findByText('no plan yet')).toBeTruthy();
  });

  it('reloads the plan when the project or its progress changes, and drops a late answer for the previous one', async () => {
    let answerOnboarding: (plan: Plan) => void = () => undefined;
    const tax: ProjectSummary = { ...project('p2', 'Tax paperwork', [thread('t9', 'Receipts', 'running')]), plan_progress: { done: 0, total: 0 } };
    const { view, planCalls } = await setup({
      items: [],
      plan: ({ id }) =>
        id === 'p1'
          ? new Promise<Plan>((resolve) => {
              answerOnboarding = resolve;
            })
          : { project_id: id, items: [step('a', 'done'), step('b', 'todo')], updated_at: '' },
    });
    await waitFor(() => expect(planCalls()).toEqual([{ id: 'p1' }]));
    await view.rerender({ inputs: { p: tax, items: [], now: NOW }, partialUpdate: true });
    expect(await screen.findByText('1 of 2 done')).toBeTruthy();
    answerOnboarding({ project_id: 'p1', items: [step('x', 'done'), step('y', 'done'), step('z', 'done')], updated_at: '' });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText('1 of 2 done')).toBeTruthy();
    expect(screen.queryByText('all 3 done')).toBeNull();
    // A new overview push with the same project and progress does not refetch.
    await view.rerender({ inputs: { p: { ...tax, latest_report: null }, items: [], now: NOW }, partialUpdate: true });
    expect(planCalls()).toEqual([{ id: 'p1' }, { id: 'p2' }]);
    await view.rerender({ inputs: { p: { ...tax, plan_progress: { done: 1, total: 2 } }, items: [], now: NOW }, partialUpdate: true });
    await waitFor(() => expect(planCalls()).toEqual([{ id: 'p1' }, { id: 'p2' }, { id: 'p2' }]));
  });
});
