// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '@desk/bff/contract';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { catalogItems, install } from '../test/catalog';
import { CommandPalette } from './CommandPalette';

afterEach(cleanup);
beforeEach(() => {
  window.location.hash = '#/map';
  globalStore.set({
    ...initialGlobalState(),
    overview: [
      {
        project: { id: 'p1', name: 'Onboarding revamp', goal: 'Relaunch onboarding', updated_at: 't' },
        desk_status: 'idle',
        threads: [{ id: 't1', title: 'Welcome emails', status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: 't', updated_at: 't' }],
        latest_report: null,
        plan_progress: { done: 0, total: 0 },
        attention_count: 0,
      } as unknown as ProjectSummary,
    ],
  });
});

function setup() {
  return installBridge({
    'skills.list': ({ projectId }: { projectId?: string }) =>
      projectId ? [{ name: 'brand-voice', scope: 'project', description: 'House tone', dir: '', version: 1 }] : [{ name: 'email-sequence', scope: 'global', description: 'Sequences', dir: '', version: 2 }],
    'library.list': () => [{ id: 'a', project_id: 'p1', path: 'emails/welcome.md', title: 'Welcome email draft', kind: 'report', origin: 'user', description: '', created_at: 't' }],
    'catalog.list': () => catalogItems({ 'pre-mortem': [install()] }),
    'memory.list': ({ q }: { q: string }) => (q.includes('email') ? [{ id: 'm1', project_id: 'p1', kind: 'decision', content: 'Send emails on Tuesdays', source: 'user', supersedes: null, superseded_by: null, created_at: 't' }] : []),
  });
}

describe('CommandPalette', () => {
  it('opens with ⌘K and jumps to threads, skills, files and memory search', async () => {
    const bridge = setup();
    render(<CommandPalette />);
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    const dialog = screen.getByRole('dialog', { name: 'Search Desk' });
    expect(within(dialog).getAllByRole('option').map((o) => o.textContent)).toContain('Onboarding revampRelaunch onboarding');
    const box = within(dialog).getByRole('combobox');
    fireEvent.change(box, { target: { value: 'email' } });
    await waitFor(() => expect(within(dialog).getByText('Send emails on Tuesdays')).toBeTruthy());
    const titles = within(dialog).getAllByRole('option').map((o) => o.querySelector('.palette-title')?.textContent);
    expect(titles).toEqual(['Welcome emails', 'email-sequence', 'Welcome email draft', 'Send emails on Tuesdays']);
    expect(bridge.calls.find((c) => c.channel === 'memory.list')?.input).toEqual({ projectId: 'p1', q: 'email' });
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(window.location.hash).toBe('#/p/p1/library?file=emails%2Fwelcome.md');
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'brand' } });
    await waitFor(() => expect(screen.getByText('brand-voice')).toBeTruthy());
    fireEvent.click(screen.getByText('brand-voice'));
    expect(window.location.hash).toBe('#/skills/project%3Ap1%3Abrand-voice');
  });

  it('offers catalog skills that are not installed yet, opening their review', async () => {
    setup();
    render(<CommandPalette />);
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'install' } });
    await waitFor(() => expect(screen.getByText('Install Paper lookup')).toBeTruthy());
    expect(screen.queryByText('Install Pre-mortem')).toBeNull();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'paper' } });
    fireEvent.click(await screen.findByText('Install Paper lookup'));
    expect(window.location.hash).toBe('#/skills/catalog/paper-lookup');
  });
});
