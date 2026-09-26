import { TestBed } from '@angular/core/testing';
import { render, screen, waitFor, within } from '@testing-library/angular';
import type { AgentStatus, ProjectSummary } from '@desk/protocol';
import { beforeEach, describe, expect, it } from 'vitest';
import { Unread } from '../core/unread';
import { ProjectList } from './project-list';

const thread = (id: string, title: string, status: AgentStatus): ProjectSummary['threads'][number] => ({ id, title, status, reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '2026-09-24T10:00:00.000Z', updated_at: '2026-09-24T10:00:00.000Z' });
const project = (id: string, name: string, threads: ProjectSummary['threads'], attention = 0): ProjectSummary => ({
  project: { id, name, goal: `${name} goal`, updated_at: '2026-09-24T10:00:00.000Z' },
  desk_status: 'idle',
  threads,
  latest_report: { headline: `${name}: research is in`, ts: '2026-09-24T11:24:00.000Z' },
  plan_progress: { done: 1, total: 3 },
  attention_count: attention,
});

const onboarding = project('p1', 'Onboarding revamp', [thread('t1', 'Funnel analysis', 'running'), thread('t2', 'Signup checklist', 'waiting')], 1);
const tax: ProjectSummary = { ...project('p2', 'Tax paperwork', [thread('t3', 'Receipts', 'done')]), project: { id: 'p2', name: 'Tax paperwork', goal: '', updated_at: '2026-09-24T10:00:00.000Z' } };
const quiet: ProjectSummary = { ...project('p3', 'Quiet', []), desk_status: 'waiting', latest_report: null };

beforeEach(() => localStorage.clear());

describe('ProjectList', () => {
  it("links each project to its conversation with its goal, summary, latest report and what needs you", async () => {
    await render(ProjectList, { inputs: { projects: [onboarding, tax, quiet] } });
    expect(document.querySelector('.project-list')!.children).toHaveLength(3);
    const cards = screen.getAllByRole('link');
    expect(cards.map((a) => a.getAttribute('href'))).toEqual(['#/p/p1/conversation', '#/p/p2/conversation', '#/p/p3/conversation']);
    expect(cards.map((a) => a.className)).toEqual(['card project-card', 'card project-card', 'card project-card']);
    const first = within(cards[0]!);
    expect(first.getByRole('heading', { level: 2 }).textContent).toBe('Onboarding revamp');
    expect(first.getByText('Onboarding revamp goal').className).toBe('subtitle');
    expect(first.getByText('1 running · 1 waiting').className).toBe('muted');
    expect(first.getByText('Onboarding revamp: research is in')).toBeTruthy();
    expect(first.getByText('1 need you').className).toBe('needs-count');
    expect(cards[1]!.querySelector('.subtitle')).toBeNull();
    expect(cards[1]!.querySelector('.needs-count')).toBeNull();
    expect(within(cards[1]!).getByText('1 done')).toBeTruthy();
    // Without attention items the list never says "on you" (projectSummaryLine(p), as on the desktop).
    expect(within(cards[2]!).getByText('Desk is waiting')).toBeTruthy();
    expect(within(cards[2]!).queryByText('Quiet: research is in')).toBeNull();
  });

  it('marks projects with activity since the last visit', async () => {
    await render(ProjectList, { inputs: { projects: [onboarding, tax, quiet] } });
    const [p1, p2, p3] = screen.getAllByRole('link');
    expect(p1!.querySelector('h2 .unread-dot')?.getAttribute('aria-label')).toBe('unread');
    expect(p2!.querySelector('.unread-dot')).toBeTruthy();
    // Nothing has happened in Quiet: no report, no thread.
    expect(p3!.querySelector('.unread-dot')).toBeNull();
    TestBed.inject(Unread).markSeen('p1', '2026-09-24T12:00:00.000Z');
    await waitFor(() => expect(p1!.querySelector('.unread-dot')).toBeNull());
    expect(p2!.querySelector('.unread-dot')).toBeTruthy();
  });
});
