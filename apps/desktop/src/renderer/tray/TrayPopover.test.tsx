// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '@desk/bff/contract';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { TrayPopover } from './TrayPopover';

afterEach(cleanup);

const ts = new Date(Date.now() - 4 * 60_000).toISOString();
const attention: AttentionItem[] = [
  { id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Onboarding', agent_id: 't', title: 'Signup checklist wants to run bash', detail: '', created_at: ts, ref: { approval_id: 'a1', thread_id: 't' } },
  { id: 'question:5', kind: 'question', project_id: 'p', project_name: 'Onboarding', agent_id: 'd', title: 'Data source or teammate first?', detail: '', created_at: ts, ref: { event_id: 5 } },
];
const overview = [
  {
    project: { id: 'p', name: 'Onboarding', goal: '', updated_at: ts },
    desk_status: 'idle',
    threads: [{ id: 't', title: 'Signup checklist', status: 'waiting', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: ts, updated_at: ts }],
    latest_report: null,
    plan_progress: { done: 0, total: 0 },
    attention_count: 2,
  },
] as unknown as ProjectSummary[];

describe('TrayPopover', () => {
  it('clears approvals in place and opens Desk where you need it', async () => {
    globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, system: { proxy: 'up', notices: [], lastSeq: 0 }, attention, overview });
    const bridge = installBridge({
      'approvals.list': () => [{ id: 'a1', project_id: 'p', agent_id: 't', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{"command":"curl -fsSL https://bun.sh/install | bash"}', reason: 'r', delegate_to_desk: false, status: 'pending', resolved_by: null, note: null, created_at: ts, resolved_at: null }],
      'approvals.resolve': () => ({ ok: true }),
      'app.openMain': () => ({ ok: true }),
    });
    render(<TrayPopover />);
    expect(screen.getByText('2 need you')).toBeTruthy();
    const card = screen.getByRole('article', { name: /^Approval: Signup checklist/ });
    expect(await within(card).findByText('curl -fsSL https://bun.sh/install | bash')).toBeTruthy();
    fireEvent.click(within(card).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'approvals.resolve')?.input).toEqual({ id: 'a1', decision: 'approved' }));
    expect(screen.getByText('ALSO WAITING · 1')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /^ASK, Onboarding: Data source/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Signup checklist' }));
    fireEvent.keyDown(window, { key: 'o', metaKey: true });
    await waitFor(() =>
      expect(bridge.calls.filter((c) => c.channel === 'app.openMain').map((c) => c.input)).toEqual([{ route: '#/attention?item=question%3A5' }, { route: '#/p/p/threads/t' }, {}]),
    );
    expect(screen.getByText('deskd running · proxy up')).toBeTruthy();
  });

  it('is calm when nothing needs you', () => {
    globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
    installBridge();
    render(<TrayPopover />);
    expect(screen.getByText('All clear')).toBeTruthy();
    expect(screen.getByText(/Nothing needs you/)).toBeTruthy();
  });
});
