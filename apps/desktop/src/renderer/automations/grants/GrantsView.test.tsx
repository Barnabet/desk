// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Grant } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { sessionOf } from '../../test/session';
import { GrantsView } from './GrantsView';

afterEach(cleanup);

const fetch: Grant = { tool: 'web_fetch', match: { domain: 'news.bbc.co.uk' }, action: 'allow' };
const bash: Grant = { tool: 'bash', match: { command: '^ls$' }, action: 'allow' };
const events = [
  ev(1, 'automation.grants_set', { automation_id: 'a1', grants: [fetch], reason: 'enabled' }),
  ev(2, 'automation.grants_set', { automation_id: 'a1', grants: [fetch, bash], reason: 'remembered', source: { run_id: 'r1', step_id: 'sum' } }),
];
const setGrantsInput = (calls: Array<{ channel: string; input: unknown }>) => calls.find((c) => c.channel === 'automations.setGrants')?.input;

describe('GrantsView', () => {
  it('lists grants with where each came from, and links a remembered one to its run', () => {
    installBridge();
    render(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch, bash] })} onChange={() => {}} />);
    const web = screen.getByRole('listitem', { name: 'Allow web_fetch on news.bbc.co.uk' });
    expect(within(web).getByText(/^Set when you turned it on/)).toBeTruthy();
    const shell = screen.getByRole('listitem', { name: 'Allow bash matching ^ls$' });
    expect(within(shell).getByText(/^Remembered at Summarise/)).toBeTruthy();
    expect(within(shell).getByRole('link', { name: 'Open run' }).getAttribute('href')).toBe('#/p/p/automations/a1/runs/r1');
    expect(within(shell).queryByRole('button', { name: /Widen/ })).toBeNull();
  });

  it('widens a web grant, removes one and saves each change as edited', async () => {
    const onChange = vi.fn();
    const bridge = installBridge({ 'automations.setGrants': (i: { grants: Grant[] }) => automationDetail({ grants: i.grants }) });
    const { rerender } = render(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch, bash] })} onChange={onChange} />);
    fireEvent.click(within(screen.getByRole('listitem', { name: 'Allow web_fetch on news.bbc.co.uk' })).getByRole('button', { name: 'Widen to bbc.co.uk' }));
    await waitFor(() =>
      expect(setGrantsInput(bridge.calls)).toEqual({
        id: 'a1',
        grants: [
          { tool: 'web_fetch', match: { domain: 'bbc.co.uk' }, action: 'allow' },
          { tool: 'web_fetch', match: { domain: '*.bbc.co.uk' }, action: 'allow' },
          bash,
        ],
        reason: 'edited',
      }),
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    bridge.calls.length = 0;
    rerender(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch, bash] })} onChange={onChange} />);
    fireEvent.click(within(screen.getByRole('listitem', { name: 'Allow bash matching ^ls$' })).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(setGrantsInput(bridge.calls)).toEqual({ id: 'a1', grants: [fetch], reason: 'edited' }));
  });

  it('adds a grant after checking it, and edits one', async () => {
    const bridge = installBridge({ 'automations.setGrants': (i: { grants: Grant[] }) => automationDetail({ grants: i.grants }) });
    render(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch] })} onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add grant' }));
    const form = screen.getByRole('form', { name: 'New grant' });
    fireEvent.change(within(form).getByLabelText('Tool'), { target: { value: 'skill_run' } });
    fireEvent.change(within(form).getByLabelText('Matches'), { target: { value: 'command' } });
    fireEvent.change(within(form).getByLabelText('Command (a regular expression)'), { target: { value: '(' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save grant' }));
    expect(within(form).getByRole('alert').textContent).toBe('Not a valid regular expression.');
    expect(setGrantsInput(bridge.calls)).toBeUndefined();
    fireEvent.change(within(form).getByLabelText('Command (a regular expression)'), { target: { value: '^digest/fetch\\.py(\\s|$)' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save grant' }));
    await waitFor(() =>
      expect(setGrantsInput(bridge.calls)).toEqual({ id: 'a1', grants: [fetch, { tool: 'skill_run', action: 'allow', match: { command: '^digest/fetch\\.py(\\s|$)' } }], reason: 'edited' }),
    );
    bridge.calls.length = 0;
    fireEvent.click(within(screen.getByRole('listitem', { name: 'Allow web_fetch on news.bbc.co.uk' })).getByRole('button', { name: 'Edit' }));
    const edit = screen.getByRole('form', { name: 'Edit grant' });
    expect((within(edit).getByLabelText('Domain (a host, or *.example.com)') as HTMLInputElement).value).toBe('news.bbc.co.uk');
    fireEvent.change(within(edit).getByLabelText('Action'), { target: { value: 'deny' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Save grant' }));
    await waitFor(() => expect(setGrantsInput(bridge.calls)).toEqual({ id: 'a1', grants: [{ ...fetch, action: 'deny' }], reason: 'edited' }));
  });

  it('shows the changes since the grants were set while they are suspended, and keeps them', async () => {
    const before = digestDef();
    before.steps = before.steps.filter((s) => s.id !== 'ok');
    before.edges = before.edges.filter((e) => e.to !== 'ok');
    const onChange = vi.fn();
    const bridge = installBridge({
      'automations.versions': () => [
        { version: 7, origin: 'agent:d1', via: 'tool', change_note: 'Asks you before publishing', created_at: '2026-09-27T10:00:00.000Z', tested: false },
        { version: 6, origin: 'user', via: 'editor', change_note: '', created_at: '2026-09-26T10:00:00.000Z', tested: true },
      ],
      'automations.version': () => ({ version: 6, definition: before, origin: 'user', change_note: '', via: 'editor', created_at: '2026-09-26T10:00:00.000Z' }),
      'automations.keepGrants': () => automationDetail({ grants: [fetch], grants_suspended: false, grants_set_version: 7 }),
    });
    render(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch], grants_suspended: true, grants_set_version: 6 })} onChange={onChange} />);
    const banner = screen.getByRole('region', { name: 'Grants suspended' });
    expect(await within(banner).findByText('Asks you before publishing')).toBeTruthy();
    const changes = within(banner).getByRole('region', { name: 'What changed since v6' });
    expect(await within(changes).findByText('Publish?')).toBeTruthy();
    fireEvent.click(within(banner).getByRole('button', { name: 'Keep grants' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.keepGrants')?.input).toEqual({ id: 'a1' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ grants_suspended: false })));
  });
});
