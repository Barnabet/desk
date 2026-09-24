// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import { DEFAULT_POLICY, RISKY_COMMAND_PATTERN, type StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { SettingsScreen } from './SettingsScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  window.location.hash = '#/p/p/settings';
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
});

const settings = { desk_model: 'claude-opus-5-5', thread_model: 'claude-opus-5-5', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: DEFAULT_POLICY };
const overview = () =>
  ({
    project: { id: 'p', name: 'Tax 2026', goal: 'File on time', instructions: '', settings, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [{ id: 's1', project_id: 'p', path: '/Users/me/tax', kind: 'folder', label: 'tax', agent_write: true, created_at: 't' }],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;
const levels: Record<string, { reasoning_efforts: string[]; default_reasoning_effort: string | null }> = {
  'claude-opus-5-5': { reasoning_efforts: ['low', 'medium', 'high', 'xhigh', 'max'], default_reasoning_effort: 'medium' },
  'claude-fable-5-1': { reasoning_efforts: [], default_reasoning_effort: null },
  'gpt-6-sol': { reasoning_efforts: ['low', 'high'], default_reasoning_effort: null },
};
const models = Object.entries(levels).map(([id, l]) => ({ id, family: 'claude', context_window: 1, max_output_tokens: 1, ...l, concurrency: 1 }));

function setup(extra: Record<string, (input: any) => unknown> = {}, events: StoredEvent[] = []) {
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    'models.list': () => models,
    'projects.update': () => ({}),
    ...extra,
  });
  startSessionRouting();
  render(<SettingsScreen projectId="p" />);
  return bridge;
}

const updates = (bridge: ReturnType<typeof installBridge>) => bridge.calls.filter((c) => c.channel === 'projects.update').map((c) => c.input);

describe('SettingsScreen', () => {
  it('saves the name and goal, and how Desk works', async () => {
    const bridge = setup();
    fireEvent.change(await screen.findByLabelText('Goal'), { target: { value: 'File by April' } });
    const about = screen.getByRole('region', { name: 'About this project' });
    fireEvent.click(within(about).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updates(bridge)[0]).toEqual({ id: 'p', patch: { name: 'Tax 2026', goal: 'File by April', instructions: '' } }));

    const style = screen.getByRole('region', { name: 'How Desk works' });
    fireEvent.click(within(style).getByLabelText(/Detailed/));
    fireEvent.click(within(style).getByLabelText(/Ask before dispatching/));
    await waitFor(() => expect((within(style).getByLabelText("Threads' model") as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(within(style).getByLabelText('Fallback when rate limited'), { target: { value: 'claude-fable-5-1' } });
    fireEvent.click(within(style).getByRole('button', { name: '6 threads at once' }));
    // Reasoning levels follow the chosen model; a model change drops a level the new model does not take.
    const deskEffort = within(style).getByLabelText("Desk's reasoning effort") as HTMLSelectElement;
    expect([...deskEffort.options].map((o) => o.textContent)).toEqual(['Model default (medium)', 'low', 'medium', 'high', 'xhigh', 'max']);
    fireEvent.change(within(style).getByLabelText("Threads' reasoning effort"), { target: { value: 'xhigh' } });
    fireEvent.change(within(style).getByLabelText("Threads' model"), { target: { value: 'gpt-6-sol' } });
    expect((within(style).getByLabelText("Threads' reasoning effort") as HTMLSelectElement).value).toBe('');
    fireEvent.change(within(style).getByLabelText("Threads' model"), { target: { value: 'claude-fable-5-1' } });
    expect((within(style).getByLabelText("Threads' reasoning effort") as HTMLSelectElement).disabled).toBe(true);
    expect(style.textContent).toContain('claude-fable-5-1 takes no reasoning level');
    fireEvent.change(within(style).getByLabelText("Threads' model"), { target: { value: 'claude-opus-5-5' } });
    fireEvent.change(within(style).getByLabelText("Threads' reasoning effort"), { target: { value: 'xhigh' } });
    fireEvent.click(within(style).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(updates(bridge)[1]).toEqual({
        id: 'p',
        patch: {
          settings: {
            desk_model: 'claude-opus-5-5',
            thread_model: 'claude-opus-5-5',
            fallback_model: 'claude-fable-5-1',
            desk_reasoning_effort: null,
            thread_reasoning_effort: 'xhigh',
            max_concurrent_threads: 6,
            check_in: 'detailed',
            autonomy: 'ask-before-dispatch',
            review_rounds: 2,
          },
        },
      }),
    );
  });

  it('edits the policy in order, resets to the default, and shows the risky pattern by name', async () => {
    const bridge = setup();
    const policy = await screen.findByRole('region', { name: 'Policy' });
    expect(within(policy).getAllByText('risky commands')).toHaveLength(4);
    expect(within(policy).getByText('This is the default policy.')).toBeTruthy();
    fireEvent.click(within(policy).getByRole('button', { name: 'Add rule' }));
    fireEvent.change(within(policy).getByLabelText('Rule 10 tool'), { target: { value: 'web_fetch' } });
    fireEvent.change(within(policy).getByLabelText('Rule 10 match'), { target: { value: 'domain' } });
    fireEvent.change(within(policy).getByLabelText('Rule 10 pattern'), { target: { value: '*.internal' } });
    fireEvent.change(within(policy).getByLabelText('Rule 10 action'), { target: { value: 'deny' } });
    for (let i = 10; i > 1; i--) fireEvent.click(within(policy).getByRole('button', { name: `Move rule ${i} up` }));
    fireEvent.click(within(policy).getByRole('button', { name: 'Save policy' }));
    await waitFor(() => expect(updates(bridge)).toHaveLength(1));
    const saved = (updates(bridge)[0] as { patch: { settings: { policy: unknown[] } } }).patch.settings.policy;
    expect(saved[0]).toEqual({ tool: 'web_fetch', match: { domain: '*.internal' }, action: 'deny' });
    expect(saved[1]).toEqual({ tool: 'bash', match: { command: RISKY_COMMAND_PATTERN }, action: 'ask' });
    fireEvent.click(within(policy).getByRole('button', { name: 'Reset to the default policy' }));
    expect(within(policy).getByText('This is the default policy.')).toBeTruthy();
  });

  it('adds and removes sources, and archives the project after confirming', async () => {
    const bridge = setup({ 'app.pickFolder': () => '/Users/me/repo', 'projects.addSource': () => ({}), 'projects.removeSource': () => ({ ok: true }), 'projects.setSourceWrite': () => ({}), 'projects.archive': () => ({ ok: true }) });
    const sources = await screen.findByRole('region', { name: 'Sources' });
    const write = within(sources).getByLabelText('Agents can write here') as HTMLInputElement;
    expect(write.checked).toBe(true);
    fireEvent.click(write);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.setSourceWrite')?.input).toEqual({ id: 'p', sourceId: 's1', agentWrite: false }));
    fireEvent.click(within(sources).getByRole('button', { name: 'Add folder…' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.addSource')?.input).toEqual({ id: 'p', source: { path: '/Users/me/repo' } }));
    fireEvent.click(within(sources).getByRole('button', { name: 'Remove tax' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.removeSource')?.input).toEqual({ id: 'p', sourceId: 's1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive project…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
  });

  it('follows the live project after a save', async () => {
    setup({}, [ev(9, 'project.updated', { goal: 'File by April' })]);
    await waitFor(() => expect((screen.getByLabelText('Goal') as HTMLTextAreaElement).value).toBe('File by April'));
  });
});
