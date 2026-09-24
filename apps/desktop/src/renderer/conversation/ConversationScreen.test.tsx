// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { ConversationScreen } from './ConversationScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  localStorage.clear();
});

const overview = (): ProjectOverview => ({
  project: { id: 'p', name: 'Onboarding revamp', goal: 'g', instructions: '', settings: { desk_model: 'claude-opus-5-5', thread_model: 'm', fallback_model: null, desk_reasoning_effort: null, thread_reasoning_effort: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'claude-opus-5-5', reasoning_effort: null, title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: { project_id: 'p', items: [{ id: '1', title: 'Add a setup checklist', status: 'in_progress', thread_ids: ['t'], notes: '' }], updated_at: 't' },
  threads: [],
  approvals: [],
  last_seq: 1,
});

const events = [
  ev(1, 'project.created', { name: 'Onboarding revamp', goal: 'g', instructions: '' }),
  ev(2, 'message.user', { text: 'Relaunch onboarding next month' }, { agent: 'd' }),
  ev(3, 'agent.created', { role: 'thread', model: 'm', title: 'Signup checklist', brief: 'Build it', workspace_path: '/w/t', parent_id: 'd' }, { agent: 't' }),
  ev(4, 'agent.status_changed', { status: 'running' }, { agent: 't' }),
  ev(5, 'report', { headline: 'Research is in', progress: 'All three competitors lead with one first win.', needs_you: ['Approve installing bun'], results: ['competitor-onboarding.md'] }, { agent: 'd' }),
  ev(6, 'question.asked', { question: 'Data source or teammate invite first?', options: ['Connect a data source', 'Invite a teammate'] }, { agent: 'd' }),
];

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, attention: [{ id: 'report:5:0', kind: 'needs_you', project_id: 'p', project_name: 'Onboarding revamp', agent_id: 'd', title: 'Approve installing bun', detail: '', created_at: '', ref: { event_id: 5 } }] });
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    'projects.send': () => ({ ok: true }),
    ...extra,
  });
  startSessionRouting();
  render(<ConversationScreen projectId="p" />);
  return bridge;
}

describe('ConversationScreen', () => {
  it('shows the line diagram, the chat, the report and the plan', async () => {
    setup();
    await screen.findByRole('heading', { name: 'Research is in' });
    expect(screen.getAllByRole('link', { name: /Signup checklist/ })[0]!.getAttribute('href')).toBe('#/p/p/threads/t');
    expect(screen.getByRole('button', { name: /Your brief|Relaunch onboarding/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'competitor-onboarding.md' }).getAttribute('href')).toBe('#/p/p/library?file=competitor-onboarding.md');
    expect(screen.getByRole('link', { name: 'Approve installing bun' }).getAttribute('href')).toBe('#/attention?item=report%3A5%3A0');
    const plan = screen.getByRole('complementary', { name: 'Plan and Desk' });
    expect(plan.textContent).toContain('Add a setup checklist');
    expect(plan.textContent).toContain('claude-opus-5-5');
  });

  it('answers a question with an option and shows the message as sending', async () => {
    const bridge = setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect a data source' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p', text: 'Connect a data source' }));
    expect(await screen.findByText('You · sending…')).toBeTruthy();
    bridge.emit('desk:event', ev(7, 'message.user', { text: 'Connect a data source' }, { agent: 'd' }));
    await waitFor(() => expect(screen.queryByText('You · sending…')).toBeNull());
    expect(screen.queryByRole('button', { name: 'Connect a data source' })).toBeNull();
  });

  it('sends with Enter and attaches files through the Library', async () => {
    const bridge = setup({ 'library.upload': ({ file }: { file: { name: string } }) => ({ id: 'a1', path: `uploads/${file.name}` }) });
    const box = (await screen.findByLabelText('Message Desk')) as HTMLTextAreaElement;
    fireEvent.change(screen.getByTestId('attach-input'), { target: { files: [new File(['hi'], 'notes.md')] } });
    await waitFor(() => expect(box.value).toContain('Attached: uploads/notes.md'));
    expect(bridge.calls.find((c) => c.channel === 'library.upload')?.input).toEqual({ projectId: 'p', file: { name: 'notes.md', content_base64: 'aGk=' } });
    fireEvent.change(box, { target: { value: 'Use these notes' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'projects.send').map((c) => c.input)).toEqual([{ id: 'p', text: 'Use these notes' }]));
    await waitFor(() => expect(box.value).toBe(''));
  });
});
