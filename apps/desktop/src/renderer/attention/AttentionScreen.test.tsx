// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { AttentionItem, StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { toastStore } from '../components/Toast';
import { useRoute } from '../router';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { AttentionScreen } from './AttentionScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  window.location.hash = '#/attention';
  toastStore.set([]);
});

const recent = new Date(Date.now() - 4 * 60_000).toISOString();
// Past the hour by more than the shared clock's 15 s step (state/now.ts), so it reads "1h" whichever side of a tick it renders.
const older = new Date(Date.now() - 60 * 60_000 - 20_000).toISOString();
const items: AttentionItem[] = [
  { id: 'report:9:0', kind: 'needs_you', project_id: 'p', project_name: 'Tax 2026', agent_id: 'd', title: 'Upload the 1099', detail: 'Research is in', created_at: older, ref: { event_id: 9 } },
  { id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Tax 2026', agent_id: 't', title: 'Signup checklist wants to run bash', detail: 'Policy rule {"tool":"bash"} → ask', created_at: recent, ref: { approval_id: 'a1', thread_id: 't' } },
  { id: 'question:7', kind: 'question', project_id: 'p', project_name: 'Tax 2026', agent_id: 'd', title: 'Data source or teammate first?', detail: '', created_at: recent, ref: { event_id: 7, options: ['Data source', 'Teammate'] } },
];

const overview = () =>
  ({
    project: { id: 'p', name: 'Tax 2026', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

const events: StoredEvent[] = [
  ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Signup checklist', brief: 'b', workspace_path: '/data/workspaces/signup-checklist', parent_id: 'd', git: { source_id: 's', branch: 'desk/signup-checklist', base: 'main', common_dir: '/r/.git' } }, { agent: 't' }),
  ev(2, 'assistant.message', { run_id: 'r', content: 'I will install bun, then run the tests.', tool_calls: [] }, { agent: 't' }),
  ev(3, 'approval.requested', { approval_id: 'a1', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{"command":"curl -fsSL https://bun.sh/install | bash"}', reason: 'Policy rule {"tool":"bash"} → ask', delegate_to_desk: false }, { agent: 't' }),
];

function Routed() {
  const r = useRoute();
  return <AttentionScreen {...(r.name === 'attention' && r.item ? { itemId: r.item } : {})} />;
}

function setup(extra: Record<string, (input: any) => unknown> = {}, list = items) {
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, attention: list });
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...extra,
  });
  startSessionRouting();
  render(<Routed />);
  return bridge;
}

describe('AttentionScreen', () => {
  it('racks strips by bay and inspects the first one with the full request', async () => {
    setup();
    const rack = screen.getByRole('region', { name: 'Strip rack' });
    expect(within(rack).getByRole('group', { name: /^CLEARANCE/ }).textContent).toContain('APR');
    expect(within(rack).getByRole('group', { name: /^QUERIES/ }).textContent).toContain('Data source or teammate first?');
    expect(screen.getByText('3 items across 1 project · oldest 1h')).toBeTruthy();
    await waitFor(() => expect(window.location.hash).toBe('#/attention?item=approval%3Aa1'));
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    expect((await within(insp).findByLabelText('Command')).textContent).toBe('$ curl -fsSL https://bun.sh/install | bash');
    expect(insp.textContent).toContain('desk/signup-checklist');
    expect(insp.textContent).toContain('workspaces/signup-checklist');
    expect(await within(insp).findByText('I will install bun, then run the tests.')).toBeTruthy();
    expect(insp.textContent).toContain('1 of 3');
  });

  it('approves with ⌘⏎ and sends the note', async () => {
    const bridge = setup({ 'approvals.resolve': () => ({ ok: true }) });
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    fireEvent.change(within(insp).getByLabelText('Note to the thread (optional)'), { target: { value: 'Use npm test' } });
    fireEvent.keyDown(window, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'approvals.resolve')?.input).toEqual({ id: 'a1', decision: 'approved', note: 'Use npm test' }));
  });

  it('explains a 409 with who decided', async () => {
    setup({
      'approvals.resolve': () => {
        throw { code: 'conflict', message: 'already resolved', status: 409 };
      },
      'approvals.list': () => [{ id: 'a1', resolved_by: 'desk', status: 'approved' }],
    });
    fireEvent.click(await screen.findByRole('button', { name: /^Deny/ }));
    await waitFor(() => expect(toastStore.get().map((t) => t.message)).toContain('Already decided by Desk.'));
  });

  it('moves with J and K, answers a question, and dismisses a hand-off', async () => {
    const bridge = setup({ 'projects.send': () => ({ ok: true }), 'attention.dismiss': () => ({ ok: true }) });
    await screen.findByRole('article', { name: /clearance request/ });
    fireEvent.keyDown(window, { key: 'j' });
    const q = await screen.findByRole('article', { name: /question from desk/ });
    fireEvent.click(within(q).getByRole('button', { name: 'Data source' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p', text: 'Data source' }));
    expect(await within(q).findByText(/^Sent\./)).toBeTruthy();
    fireEvent.keyDown(window, { key: 'j' });
    const h = await screen.findByRole('article', { name: /from a report/ });
    fireEvent.click(within(h).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'attention.dismiss')?.input).toEqual({ id: 'report:9:0' }));
    fireEvent.keyDown(window, { key: 'e' });
    expect(window.location.hash).toBe('#/p/p/conversation');
  });

  it('is all clear when nothing needs you', () => {
    setup({}, []);
    expect(screen.getByRole('heading', { name: 'All clear' })).toBeTruthy();
  });
});
