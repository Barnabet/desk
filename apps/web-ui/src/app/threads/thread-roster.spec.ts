import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import { emptyMessages, type ProjectState, type ThreadView } from '@desk/client';
import { FakeDeskBridge, provideGlobal } from '../testing/fake-bridge';
import { ThreadRoster } from './thread-roster';

const at = (m: number) => `2026-09-24T10:${String(m).padStart(2, '0')}:00.000Z`;

const thread = (id: string, extra: Partial<ThreadView> = {}): ThreadView =>
  ({
    id,
    project_id: 'p',
    role: 'thread',
    status: 'idle',
    model: 'claude-opus-5-5',
    reasoning_effort: null,
    title: id,
    brief: 'b',
    workspace_path: '/w',
    parent_id: 'd',
    inbox_cursor: 0,
    review_round: 0,
    result_summary: null,
    result_artifacts: null,
    active_skills: [],
    git_source_id: null,
    git_branch: null,
    git_base: null,
    git_common_dir: null,
    automation_run_id: null,
    automation_step_id: null,
    archived_at: null,
    created_at: at(0),
    updated_at: 't',
    activity: null,
    reason: null,
    model_override: null,
    effort: null,
    ...extra,
  }) as unknown as ThreadView;

const project = (threads: ThreadView[]) => ({ project: { id: 'p', name: 'Onboarding', settings: { review_rounds: 2 } }, threads }) as unknown as ProjectState;

async function mount(threads: ThreadView[]) {
  const bridge = new FakeDeskBridge();
  await render(ThreadRoster, {
    inputs: { project: project(threads), messages: emptyMessages(), now: Date.parse(at(30)) },
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } })],
  });
}

const titles = () => [...document.querySelectorAll('.thread-card h2')].map((h) => h.textContent);

describe('ThreadRoster', () => {
  it('puts the busiest threads first and the newest first within a status, and shows archived ones on request', async () => {
    await mount([
      thread('a', { title: 'Done one', status: 'done', created_at: at(0) }),
      thread('b', { title: 'Older run', status: 'running', created_at: at(1) }),
      thread('c', { title: 'Newer run', status: 'running', created_at: at(5) }),
      thread('d', { title: 'Waiting one', status: 'waiting', created_at: at(2) }),
      thread('e', { title: 'Queued one', status: 'queued', created_at: at(3) }),
      thread('f', { title: 'Archived one', status: 'done', created_at: at(4), archived_at: at(20) }),
    ]);
    expect(screen.getByRole('heading', { name: 'Threads', level: 1 })).toBeTruthy();
    expect(titles()).toEqual(['Waiting one', 'Newer run', 'Older run', 'Queued one', 'Done one']);
    expect(screen.getByRole('link', { name: /Newer run/ }).getAttribute('href')).toBe('#/p/p/threads/c');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show 1 archived' }));
    expect(titles()).toEqual(['Waiting one', 'Newer run', 'Older run', 'Queued one', 'Archived one', 'Done one']);
    const archived = screen.getByRole('link', { name: /Archived one/ });
    expect(archived.className).toBe('card thread-card archived');
    expect(within(archived).getByText('Archived')).toBeTruthy();
  });

  it('puts a status it does not know after every known one, even one named like a prototype member', async () => {
    await mount([
      thread('a', { title: 'Known idle', status: 'idle', created_at: at(0) }),
      thread('b', { title: 'Odd one', status: 'constructor' as ThreadView['status'], created_at: at(9) }),
    ]);
    expect(titles()).toEqual(['Known idle', 'Odd one']);
  });

  it("shows a card's status, reason, activity, facts and skills, and no reason while a thread runs", async () => {
    await mount([
      thread('t', {
        title: 'Welcome emails',
        status: 'failed',
        reason: 'Rate limited',
        activity: 'bash wc -w emails/*.md',
        model_override: 'claude-sonnet-5',
        effort: 'high',
        git_branch: 'desk/welcome-emails',
        review_round: 1,
        active_skills: ['brand-voice'],
      }),
      thread('u', { title: 'Busy one', status: 'running', reason: 'Resuming after a restart' }),
    ]);
    const card = screen.getByRole('link', { name: /Welcome emails/ });
    expect(within(card).getByText('Failed')).toBeTruthy();
    expect(within(card).getByText('Rate limited')).toBeTruthy();
    expect(within(card).getByText('bash wc -w emails/*.md').className).toBe('mono small thread-activity');
    expect([...card.querySelectorAll('.thread-facts > div')].map((d) => [d.querySelector('dt')!.textContent, d.querySelector('dd')!.textContent])).toEqual([
      ['Elapsed', '30m'],
      ['Model', 'claude-sonnet-5 · high'],
      ['Workspace', 'desk/welcome-emails'],
      ['Revision', '1 of 2'],
    ]);
    expect(within(card).getByText('brand-voice').className).toBe('skill-badge');
    const busy = screen.getByRole('link', { name: /Busy one/ });
    expect(within(busy).getByText('Running')).toBeTruthy();
    expect(within(busy).queryByText('Resuming after a restart')).toBeNull();
    expect(within(busy).getByText('scratch')).toBeTruthy();
    expect(busy.querySelectorAll('.thread-facts > div')).toHaveLength(3);
  });

  it('sends the user to the conversation when there are no threads yet', async () => {
    await mount([]);
    expect(screen.getByRole('heading', { name: 'No threads yet' })).toBeTruthy();
    expect(screen.getByText('Desk splits your brief into threads that work in parallel.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Brief Desk' }).getAttribute('href')).toBe('#/p/p/conversation');
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('is the roster page itself, with no wrapper around its head and grid', async () => {
    await mount([thread('a', { title: 'Only one' })]);
    const root = document.querySelector('.page.roster')!;
    expect([...root.children].map((c) => c.className)).toEqual(['roster-head', 'roster-grid']);
    expect([...root.querySelector('.roster-grid')!.children].map((c) => c.tagName)).toEqual(['A']);
  });
});
