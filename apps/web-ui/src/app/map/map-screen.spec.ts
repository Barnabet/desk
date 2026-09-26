import { TestBed } from '@angular/core/testing';
import { render, screen, waitFor, within } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { initialGlobalState } from '@desk/bff/contract';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { RouteService } from '../core/route.service';
import { FakeDeskBridge, type FakeHandlers } from '../testing/fake-bridge';
import { MapScreen } from './map-screen';
import { projectSummaryLine } from './project-summary';

beforeEach(() => {
  localStorage.clear();
  window.location.hash = '#/map';
});
afterEach(() => {
  vi.restoreAllMocks();
});

const thread = (id: string, title: string, status: 'running' | 'waiting' | 'done') => ({ id, title, status, reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '2026-09-24T10:00:00.000Z', updated_at: '2026-09-24T10:00:00.000Z' });
const project = (id: string, name: string, threads: ProjectSummary['threads'], attention = 0): ProjectSummary => ({
  project: { id, name, goal: `${name} goal`, updated_at: '2026-09-24T10:00:00.000Z' },
  desk_status: 'idle',
  threads,
  latest_report: { headline: `${name}: research is in`, ts: '2026-09-24T11:24:00.000Z' },
  plan_progress: { done: 1, total: 3 },
  attention_count: attention,
});
const approval: AttentionItem = { id: 'approval:a1', kind: 'approval', project_id: 'p1', project_name: 'Onboarding revamp', agent_id: 't2', title: 'Signup checklist wants to run bash', detail: 'rule 1', created_at: '2026-09-24T11:21:00.000Z', ref: { approval_id: 'a1', thread_id: 't2' } };

function seed() {
  TestBed.inject(GlobalStore).set({
    ...initialGlobalState(),
    connection: { status: 'live' },
    health: { version: '1.0.0', protocol_version: 1, proxy: 'up', uptime_s: 60 },
    overview: [project('p1', 'Onboarding revamp', [thread('t1', 'Funnel analysis', 'running'), thread('t2', 'Signup checklist', 'waiting')], 1), project('p2', 'Tax paperwork', [thread('t3', 'Receipts', 'done')])],
    attention: [approval],
  });
}

async function setup(o: { newProject?: boolean; seeded?: boolean; plan?: FakeHandlers['projects.plan'] } = {}) {
  const bridge = new FakeDeskBridge({ 'projects.plan': o.plan ?? (() => null), 'projects.create': () => ({ project: { id: 'new' } }) });
  const view = await render(MapScreen, { inputs: { newProject: o.newProject ?? false }, providers: [{ provide: DeskBridge, useValue: bridge }] });
  if (o.seeded) seed();
  view.detectChanges();
  return { bridge, user: userEvent.setup() };
}

describe('MapScreen', () => {
  it('opens the new project sheet', async () => {
    const { user } = await setup({ seeded: true });
    await user.click(screen.getByRole('button', { name: /New project/ }));
    expect(await screen.findByRole('dialog', { name: 'New project' })).toBeTruthy();
    const navigate = vi.spyOn(TestBed.inject(RouteService), 'navigate');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New project' })).toBeNull());
    expect(window.location.hash).toBe('#/map');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('counts running threads, busy projects and what waits on you', async () => {
    await setup({ seeded: true });
    expect(screen.getByRole('heading', { name: 'Projects', level: 1 })).toBeTruthy();
    expect(screen.getByText('1 thread running in 1 project. 1 thing is waiting on you.')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'No projects yet' })).toBeNull();
  });

  it('shows the empty state without projects, and opens the new project on creation', async () => {
    const { bridge, user } = await setup();
    expect(screen.getByText('0 threads running in 0 projects. 0 things are waiting on you.')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'No projects yet' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Create a project' }));
    const dialog = await screen.findByRole('dialog', { name: 'New project' });
    await user.type(screen.getByLabelText('Name'), 'Launch');
    await user.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/new/conversation'));
    await waitFor(() => expect(dialog.isConnected).toBe(false));
    expect(bridge.calls.find((c) => c.channel === 'projects.create')?.input).toEqual({ name: 'Launch', goal: '' });
  });

  it('opens on #/map?new=1 and goes back to #/map when the sheet closes', async () => {
    window.location.hash = '#/map?new=1';
    const { user } = await setup({ newProject: true, seeded: true });
    expect(await screen.findByRole('dialog', { name: 'New project' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
    expect(screen.queryByRole('dialog', { name: 'New project' })).toBeNull();
  });

  it('draws a territory per project and fills the inspector for the selected one', async () => {
    const { user } = await setup({
      seeded: true,
      plan: ({ id }) => ({
        project_id: id,
        items: [
          { id: '1', title: 'Map competitors', status: 'done', thread_ids: [], notes: '' },
          { id: '2', title: 'Funnel', status: 'in_progress', thread_ids: ['t1'], notes: '' },
          { id: '3', title: 'Launch', status: 'todo', thread_ids: [], notes: '' },
        ],
        updated_at: '',
      }),
    });
    expect(screen.getByRole('button', { name: 'Onboarding revamp: show details' })).toBeTruthy();
    for (const a of screen.getAllByRole('link', { name: /Funnel analysis/ })) expect(a.getAttribute('href')).toBe('#/p/p1/threads/t1');
    const inspector = screen.getByRole('region', { name: 'Territory' });
    expect(inspector.textContent).toContain('Onboarding revamp');
    expect(inspector.textContent).toContain('Onboarding revamp: research is in');
    await waitFor(() => expect(inspector.textContent).toContain('1 of 3 done'));
    expect(screen.getAllByRole('link', { name: 'Signup checklist wants to run bash' })[0]!.getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    await user.click(screen.getByRole('button', { name: 'Tax paperwork: show details' }));
    await waitFor(() => expect(screen.getByRole('region', { name: 'Territory' }).textContent).toContain('Tax paperwork'));
    expect(screen.getByRole('button', { name: 'Tax paperwork: show details' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('switches to the list view and remembers it', async () => {
    const { user } = await setup({ seeded: true });
    expect(screen.getByRole('button', { name: 'Map' }).getAttribute('aria-pressed')).toBe('true');
    await user.click(screen.getByRole('button', { name: 'List' }));
    expect((await screen.findByRole('link', { name: /Tax paperwork/ })).getAttribute('href')).toBe('#/p/p2/conversation');
    expect(localStorage.getItem('desk.mapView')).toBe('list');
    expect(screen.getByRole('button', { name: 'List' }).getAttribute('aria-pressed')).toBe('true');
    expect(document.querySelector('.map-canvas')).toBeNull();
    expect(document.querySelector('.map-screen > .page > .project-list')).toBeTruthy();
  });

  it('opens in the list view when that was the last choice', async () => {
    localStorage.setItem('desk.mapView', 'list');
    await setup({ seeded: true });
    expect(screen.getByRole('button', { name: 'List' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('link', { name: /Onboarding revamp/ }).getAttribute('href')).toBe('#/p/p1/conversation');
    expect(document.querySelector('.map-canvas')).toBeNull();
  });

  it('labels the amber disc "Waiting": only attention items wait on you', async () => {
    await setup({ seeded: true });
    const legend = document.querySelector<HTMLElement>('.map-legend')!;
    expect(legend.textContent).not.toContain('Waiting on you');
    expect(within(legend).getAllByText('Waiting')).toHaveLength(2);
  });

  it('lays the orbit map out in the measured canvas, leaving the inspector its column', async () => {
    vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(1300);
    vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(800);
    await setup({ seeded: true });
    await waitFor(() => expect(document.querySelector('svg.orbit-svg')?.getAttribute('width')).toBe('920'));
    expect(document.querySelector('svg.orbit-svg')!.getAttribute('height')).toBe('800');
    // The seed leaves system.proxy at initialGlobalState's 'unknown'.
    expect(screen.getByRole('button', { name: 'deskd, running, model proxy unknown' })).toBeTruthy();
    expect(Array.from(document.querySelector('.orbit-sun-label')!.children, (c) => c.textContent)).toEqual(['1.0.0 · running', 'proxy unknown · 1 thread running']);
  });
});

describe('projectSummaryLine', () => {
  it('says Desk is waiting on you only when Desk has an attention item', () => {
    const quiet = { ...project('p3', 'Quiet', []), desk_status: 'waiting' as const };
    const deskAsks: AttentionItem = { id: 'question:7', kind: 'question', project_id: 'p3', project_name: 'Quiet', agent_id: 'd3', title: 'EU or US?', detail: '', created_at: '2026-09-24T11:00:00.000Z', ref: { event_id: 7 } };
    expect(projectSummaryLine(quiet)).toBe('Desk is waiting');
    expect(projectSummaryLine(quiet, [deskAsks])).toBe('Desk is waiting on you');
    // A thread's item, or another project's, is not this Desk's.
    expect(projectSummaryLine(quiet, [{ ...approval, project_id: 'p3' }])).toBe('Desk is waiting');
    expect(projectSummaryLine(quiet, [{ ...deskAsks, project_id: 'p1' }])).toBe('Desk is waiting');
  });
});
