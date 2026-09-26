import { TestBed } from '@angular/core/testing';
import { render, screen, waitFor } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { initialGlobalState } from '@desk/bff/contract';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { RouteService } from '../core/route.service';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { MapScreen } from './map-screen';

beforeEach(() => {
  localStorage.clear();
  window.location.hash = '#/map';
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

async function setup(o: { newProject?: boolean; seeded?: boolean } = {}) {
  const bridge = new FakeDeskBridge({ 'projects.plan': () => null, 'projects.create': () => ({ project: { id: 'new' } }) });
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
});
