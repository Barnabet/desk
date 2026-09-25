// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '@desk/bff/contract';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { MapScreen } from './MapScreen';
import { projectSummaryLine } from './OrbitMap';

afterEach(cleanup);
beforeEach(() => localStorage.clear());

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
  globalStore.set({
    ...initialGlobalState(),
    connection: { status: 'live' },
    health: { version: '1.0.0', protocol_version: 1, proxy: 'up', uptime_s: 60 },
    overview: [project('p1', 'Onboarding revamp', [thread('t1', 'Funnel analysis', 'running'), thread('t2', 'Signup checklist', 'waiting')], 1), project('p2', 'Tax paperwork', [thread('t3', 'Receipts', 'done')])],
    attention: [approval],
  });
}

describe('MapScreen', () => {
  it('draws a territory per project and fills the inspector for the selected one', async () => {
    seed();
    installBridge({ 'projects.plan': ({ id }: { id: string }) => ({ project_id: id, items: [{ id: '1', title: 'Map competitors', status: 'done', thread_ids: [], notes: '' }, { id: '2', title: 'Funnel', status: 'in_progress', thread_ids: ['t1'], notes: '' }, { id: '3', title: 'Launch', status: 'todo', thread_ids: [], notes: '' }], updated_at: '' }) });
    render(<MapScreen newProject={false} />);
    expect(screen.getByRole('button', { name: 'Onboarding revamp: show details' })).toBeTruthy();
    for (const a of screen.getAllByRole('link', { name: /Funnel analysis/ })) expect(a.getAttribute('href')).toBe('#/p/p1/threads/t1');
    const inspector = screen.getByRole('region', { name: 'Territory' });
    expect(inspector.textContent).toContain('Onboarding revamp');
    expect(inspector.textContent).toContain('Onboarding revamp: research is in');
    await waitFor(() => expect(inspector.textContent).toContain('1 of 3 done'));
    expect(screen.getAllByRole('link', { name: 'Signup checklist wants to run bash' })[0]!.getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    fireEvent.click(screen.getByRole('button', { name: 'Tax paperwork: show details' }));
    expect(screen.getByRole('region', { name: 'Territory' }).textContent).toContain('Tax paperwork');
  });

  it('switches to the list view and remembers it', () => {
    seed();
    installBridge({ 'projects.plan': () => null });
    render(<MapScreen newProject={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    expect(screen.getByRole('link', { name: /Tax paperwork/ }).getAttribute('href')).toBe('#/p/p2/conversation');
    expect(localStorage.getItem('desk.mapView')).toBe('list');
  });

  it('opens the new project sheet', () => {
    seed();
    installBridge({ 'projects.plan': () => null });
    render(<MapScreen newProject={false} />);
    fireEvent.click(screen.getByRole('button', { name: /New project/ }));
    expect(screen.getByRole('dialog', { name: 'New project' })).toBeTruthy();
  });

  it('labels the amber disc "Waiting": only attention items wait on you', () => {
    seed();
    installBridge({ 'projects.plan': () => null });
    render(<MapScreen newProject={false} />);
    const legend = document.querySelector<HTMLElement>('.map-legend')!;
    expect(legend.textContent).not.toContain('Waiting on you');
    expect(within(legend).getAllByText('Waiting')).toHaveLength(2);
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
