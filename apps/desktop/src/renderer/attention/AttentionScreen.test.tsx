// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { AttentionItem, StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '@desk/bff/contract';
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

/** Another approval, from thread `t<n>`, waiting `minutes`: it racks after a1 (4 minutes) when younger. */
const approval = (n: number, minutes: number): AttentionItem => ({
  id: `approval:a${n}`,
  kind: 'approval',
  project_id: 'p',
  project_name: 'Tax 2026',
  agent_id: `t${n}`,
  title: `Thread ${n} wants to run bash`,
  detail: 'Policy rule {"tool":"bash"} → ask',
  created_at: new Date(Date.now() - minutes * 60_000).toISOString(),
  ref: { approval_id: `a${n}`, thread_id: `t${n}` },
});

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
    const note = within(insp).getByLabelText('Note to the thread (optional)');
    fireEvent.change(note, { target: { value: 'Use npm test' } });
    // ⌘⏎ approves from the note too.
    fireEvent.keyDown(note, { key: 'Enter', metaKey: true });
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

  it('racks a paused project as GND in the holding bay, and resumes it from the Inspector', async () => {
    const paused: AttentionItem = {
      id: 'paused:12',
      kind: 'paused',
      project_id: 'p',
      project_name: 'Tax 2026',
      agent_id: null,
      title: 'Agents in Tax 2026 are paused: too many automatic wakes this hour',
      detail: 'Their messages are kept. Resume, or write to any agent.',
      created_at: recent,
      ref: { event_id: 12 },
    };
    const bridge = setup({ 'attention.dismiss': () => ({ ok: true }) }, [paused]);
    const holding = within(screen.getByRole('region', { name: 'Strip rack' })).getByRole('group', { name: 'HOLDING: Stalled, failed or paused, 1' });
    const strip = within(holding).getByRole('button', { name: /^GND, Tax 2026: Agents in Tax 2026 are paused/ });
    expect(strip.getAttribute('aria-label')).toContain('. Project Tax 2026. Waiting ');
    expect(strip.querySelector('.strip-tag')!.className).toBe('strip-tag wait');
    const insp = await screen.findByRole('article', { name: 'Selected: paused project' });
    expect(insp.textContent).toContain('Their messages are kept. Resume, or write to any agent.');
    expect(insp.textContent).toContain('Resuming lets agents wake each other again.');
    expect(insp.textContent).not.toMatch(/What the thread said|Open thread|The thread is not changed/);
    expect(within(insp).getByRole('button', { name: /^Open conversation/ })).toBeTruthy();
    fireEvent.click(within(insp).getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'attention.dismiss')?.input).toEqual({ id: 'paused:12' }));
    fireEvent.keyDown(window, { key: 'e' });
    expect(window.location.hash).toBe('#/p/p/conversation');
  });

  it('explains GND in the legend', () => {
    setup();
    expect(document.querySelector('.strip-legend')!.textContent).toContain('GNDPaused project');
  });

  it('ignores a held ⌘⏎ or ⌘⌫, which would decide the next approval unseen', async () => {
    const bridge = setup({ 'approvals.resolve': () => ({ ok: true }) });
    await screen.findByRole('article', { name: /clearance request/ });
    const resolves = () => bridge.calls.filter((c) => c.channel === 'approvals.resolve').map((c) => c.input);
    fireEvent.keyDown(window, { key: 'Enter', metaKey: true, repeat: true });
    expect(resolves()).toEqual([]);
    fireEvent.keyDown(document.body, { key: 'Backspace', ctrlKey: true, repeat: true });
    expect(resolves()).toEqual([]);
    fireEvent.keyDown(document.body, { key: 'Backspace', ctrlKey: true });
    await waitFor(() => expect(resolves()).toEqual([{ id: 'a1', decision: 'denied' }]));
  });

  it('keeps the next approval pending when the one before it fails late', async () => {
    let fail: (err: unknown) => void = () => {};
    const bridge = setup(
      { 'approvals.resolve': (input: { id: string }) => new Promise((_, reject) => (input.id === 'a1' ? (fail = reject) : undefined)) },
      [...items, approval(2, 2)],
    );
    const resolves = () => bridge.calls.filter((c) => c.channel === 'approvals.resolve').map((c) => c.input);
    await waitFor(() => expect(window.location.hash).toBe('#/attention?item=approval%3Aa1'));
    await screen.findByRole('article', { name: /clearance request/ });
    fireEvent.keyDown(window, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(resolves()).toHaveLength(1));
    fireEvent.keyDown(window, { key: 'j' });
    await waitFor(() => expect(window.location.hash).toBe('#/attention?item=approval%3Aa2'));
    await waitFor(() => expect(screen.getByRole('article', { name: /clearance request/ }).textContent).toContain('2 of 4'));
    fireEvent.keyDown(window, { key: 'Enter', metaKey: true });
    const approve = await screen.findByRole('button', { name: /^Approve once/ });
    await waitFor(() => expect(approve.getAttribute('aria-busy')).toBe('true'));
    // a1's request fails only now: the toast says so, and a2's approval is still on its way.
    fail({ code: 'internal', message: 'deskd went away', status: 500 });
    await waitFor(() => expect(toastStore.get().map((t) => t.message)).toContain('deskd went away'));
    expect(approve.getAttribute('aria-busy')).toBe('true');
    fireEvent.keyDown(window, { key: 'Enter', metaKey: true });
    await new Promise((r) => setTimeout(r, 0));
    expect(resolves()).toEqual([
      { id: 'a1', decision: 'approved' },
      { id: 'a2', decision: 'approved' },
    ]);
  });

  it('denies with ⌘⌫, and leaves ⌘⌫ to a text box', async () => {
    const bridge = setup({ 'approvals.resolve': () => ({ ok: true }) });
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    await waitFor(() => expect(window.location.hash).toBe('#/attention?item=approval%3Aa1'));
    const note = within(insp).getByLabelText('Note to the thread (optional)');
    // In the note, Ctrl+⌫ deletes a word and ⌘⌫ the line: the note keeps the key (no preventDefault), and nothing is denied.
    expect(fireEvent.keyDown(note, { key: 'Backspace', ctrlKey: true })).toBe(true);
    expect(fireEvent.keyDown(note, { key: 'Backspace', metaKey: true })).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(bridge.calls.some((c) => c.channel === 'approvals.resolve')).toBe(false);
    // Outside a text box, ⌘⌫ denies.
    expect(fireEvent.keyDown(document.body, { key: 'Backspace', metaKey: true })).toBe(false);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'approvals.resolve')?.input).toEqual({ id: 'a1', decision: 'denied' }));
  });
});
