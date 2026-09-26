import { TestBed } from '@angular/core/testing';
import { render, screen, waitFor } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import type { AgentStatus, AttentionItem, ProjectSummary } from '@desk/protocol';
import { activityOf, layoutMap } from '@desk/ui-core';
import { beforeEach, describe, expect, it } from 'vitest';
import { Unread } from '../core/unread';
import { OrbitMap } from './orbit-map';

const NOW = Date.parse('2026-09-24T11:26:00.000Z');
const thread = (id: string, title: string | null, status: AgentStatus): ProjectSummary['threads'][number] => ({ id, title, status, reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '2026-09-24T10:00:00.000Z', updated_at: '2026-09-24T10:00:00.000Z' });
const project = (id: string, name: string, threads: ProjectSummary['threads'], attention = 0): ProjectSummary => ({
  project: { id, name, goal: `${name} goal`, updated_at: '2026-09-24T10:00:00.000Z' },
  desk_status: 'idle',
  threads,
  latest_report: { headline: `${name}: research is in`, ts: '2026-09-24T11:24:00.000Z' },
  plan_progress: { done: 1, total: 3 },
  attention_count: attention,
});
const approval: AttentionItem = { id: 'approval:a1', kind: 'approval', project_id: 'p1', project_name: 'Onboarding revamp', agent_id: 't2', title: 'Signup checklist wants to run bash', detail: 'rule 1', created_at: '2026-09-24T11:21:00.000Z', ref: { approval_id: 'a1', thread_id: 't2' } };
const question: AttentionItem = { id: 'question:7', kind: 'question', project_id: 'p1', project_name: 'Onboarding revamp', agent_id: 'd1', title: 'EU or US?', detail: '', created_at: '2026-09-24T11:24:00.000Z', ref: { event_id: 7 } };
const paused: AttentionItem = { id: 'paused:9', kind: 'paused', project_id: 'p2', project_name: 'Tax paperwork', agent_id: null, title: 'Agents paused in Tax paperwork', detail: '', created_at: '2026-09-24T11:21:00.000Z', ref: { event_id: 9 } };

const onboarding = project('p1', 'Onboarding revamp', [thread('t1', 'Funnel analysis', 'running'), thread('t2', 'Signup checklist', 'waiting')], 1);
const tax = project('p2', 'Tax paperwork', [thread('t3', 'Receipts', 'done')]);

beforeEach(() => {
  localStorage.clear();
  window.location.hash = '#/map';
});

async function setup(o: { projects?: ProjectSummary[]; attention?: AttentionItem[]; selected?: string | null } = {}) {
  const projects = o.projects ?? [onboarding, tax];
  const layout = layoutMap(projects.map((p) => ({ id: p.project.id, activity: activityOf(p), threads: p.threads })), 1000, 700);
  const picked: string[] = [];
  const sunLabel: [string, string] = ['1.0.0 · running', 'proxy up · 1 thread running'];
  await render(OrbitMap, {
    inputs: { layout, projects, attention: o.attention ?? [approval], selected: o.selected ?? null, width: 1000, height: 700, sunLabel, sunAria: 'deskd, running, model proxy up', now: NOW },
    on: { pick: (id: string) => picked.push(id) },
  });
  return { picked, user: userEvent.setup() };
}

describe('OrbitMap', () => {
  it('draws the rings, a disc and an orbit per territory, and a spoke per thread styled by its status', async () => {
    await setup();
    const svg = document.querySelector('svg.orbit-svg')!;
    expect(svg.getAttribute('width')).toBe('1000');
    expect(svg.getAttribute('height')).toBe('700');
    expect(svg.getAttribute('aria-hidden')).toBe('true');
    expect(Array.from(svg.children).filter((c) => c.tagName === 'circle')).toHaveLength(3);
    expect(svg.querySelectorAll('g')).toHaveLength(2);
    expect(svg.querySelector('g circle')!.namespaceURI).toBe('http://www.w3.org/2000/svg');
    const spokes = Array.from(svg.querySelectorAll('path'), (p) => [p.getAttribute('stroke'), p.getAttribute('stroke-width'), p.getAttribute('stroke-dasharray')]).sort();
    expect(spokes).toEqual([
      ['#2F5BD3', '2.5', null],
      ['#A15C00', '2', '4 4'],
      ['#B7C4E6', '1.5', '2 4'],
    ]);
  });

  it('labels each project, colours its Desk by tone and links every thread to its route', async () => {
    await setup();
    expect(Array.from(document.querySelectorAll('.orbit-label-name'), (e) => e.textContent).sort()).toEqual(['Onboarding revamp', 'Tax paperwork']);
    expect(Array.from(document.querySelectorAll('.orbit-label-sub'), (e) => e.textContent).sort()).toEqual(['1 done', '1 running · 1 waiting']);
    expect(screen.getByRole('button', { name: 'Onboarding revamp: show details' }).className).toBe('orbit-desk orbit-desk-running');
    expect(screen.getByRole('button', { name: 'Tax paperwork: show details' }).className).toBe('orbit-desk orbit-desk-idle');
    expect(screen.getByRole('button', { name: 'Tax paperwork: show details' }).textContent).toBe('Desk');
    const funnel = screen.getByRole('link', { name: 'Funnel analysis' });
    expect(funnel.getAttribute('href')).toBe('#/p/p1/threads/t1');
    expect(funnel.classList.contains('orbit-thread-running')).toBe(true);
    expect(screen.getByRole('link', { name: 'Signup checklist' }).classList.contains('orbit-thread-waiting')).toBe(true);
    expect(screen.getByRole('link', { name: 'Receipts · done' }).getAttribute('href')).toBe('#/p/p2/threads/t3');
  });

  it('pins the first item that needs you above its Desk, with the count when there are more', async () => {
    await setup({ attention: [approval, question] });
    const pin = screen.getByRole('link', { name: 'Signup checklist wants to run bash' });
    expect(pin.className).toBe('orbit-callout');
    expect(pin.getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    expect(pin.querySelector('.orbit-pin')!.textContent).toBe('2');
    expect(pin.querySelector('.orbit-callout-kind')!.textContent).toBe('Approval · 5m');
    expect(screen.queryByRole('link', { name: 'EU or US?' })).toBeNull();
  });

  it("shows one item's glyph, and a paused project's agents as its callout", async () => {
    await setup({ projects: [onboarding, project('p2', 'Tax paperwork', [thread('t3', 'Receipts', 'done')], 1)], attention: [approval, paused] });
    expect(screen.getByRole('link', { name: 'Signup checklist wants to run bash' }).querySelector('.orbit-pin')!.textContent).toBe('!');
    const pin = screen.getByRole('link', { name: 'Agents paused in Tax paperwork' });
    expect(pin.getAttribute('href')).toBe('#/attention?item=paused%3A9');
    expect(pin.querySelector('.orbit-pin')!.textContent).toBe('1');
    expect(pin.querySelector('.orbit-callout-kind')!.textContent).toBe('Agents paused · 5m');
    expect(screen.getByRole('button', { name: 'Tax paperwork: show details' }).className).toBe('orbit-desk orbit-desk-waiting');
  });

  it('marks the selected Desk and reports a press on another', async () => {
    const { picked, user } = await setup({ selected: 'p1' });
    expect(screen.getByRole('button', { name: 'Onboarding revamp: show details' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'Tax paperwork: show details' }).getAttribute('aria-pressed')).toBe('false');
    await user.click(screen.getByRole('button', { name: 'Tax paperwork: show details' }));
    expect(picked).toEqual(['p2']);
  });

  it('says how deskd is under the sun, which opens the system screen', async () => {
    const { user } = await setup();
    expect(Array.from(document.querySelector('.orbit-sun-label')!.children, (c) => c.textContent)).toEqual(['1.0.0 · running', 'proxy up · 1 thread running']);
    const sun = screen.getByRole('button', { name: 'deskd, running, model proxy up' });
    expect(sun.textContent).toBe('deskd');
    await user.click(sun);
    await waitFor(() => expect(window.location.hash).toBe('#/system'));
  });

  it('marks projects with activity since the last visit', async () => {
    await setup();
    expect(document.querySelectorAll('.orbit-label .unread-dot')).toHaveLength(2);
    TestBed.inject(Unread).markSeen('p1', '2026-09-24T12:00:00.000Z');
    await waitFor(() => expect(document.querySelectorAll('.orbit-label .unread-dot')).toHaveLength(1));
  });
});
