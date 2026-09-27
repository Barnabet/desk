// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition, Grant } from '@desk/protocol';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { NameDialog } from './NameDialog';
import { RunDialog } from './RunDialog';
import { TurnOnDialog } from './TurnOnDialog';

afterEach(cleanup);

const withInputs = (): AutomationDefinition => ({
  ...digestDef(),
  inputs: [
    { key: 'topic', label: 'Topic', type: 'text', required: true },
    { key: 'tone', label: 'Tone', type: 'choice', required: false, options: ['dry', 'warm'], default: 'dry' },
    { key: 'count', label: 'How many', type: 'number', required: false },
    { key: 'brief', label: 'Brief file', type: 'file', required: false },
    { key: 'deep', label: 'Go deep', type: 'boolean', required: false },
  ],
});

describe('RunDialog', () => {
  it('builds the form from the inputs, picks a file, and starts a test run', async () => {
    const bridge = installBridge({ 'app.pickFile': () => '/Users/me/brief.pdf', 'automations.run': () => ({ run_id: 'r9' }) });
    const onStarted = vi.fn();
    render(<RunDialog target={{ id: 'a1', name: 'digest', title: 'Weekly digest', definition: withInputs() }} test onClose={() => {}} onStarted={onStarted} />);
    expect(screen.getByRole('dialog', { name: 'Test Weekly digest' })).toBeTruthy();
    const start = screen.getByRole('button', { name: 'Start test' }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'robots' } });
    fireEvent.change(screen.getByLabelText('How many (optional)'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Choose…' }));
    await waitFor(() => expect((screen.getByLabelText('Brief file (optional)') as HTMLInputElement).value).toBe('/Users/me/brief.pdf'));
    fireEvent.click(screen.getByLabelText('Go deep'));
    fireEvent.click(start);
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('r9'));
    expect(bridge.calls.find((c) => c.channel === 'automations.run')?.input).toEqual({
      id: 'a1',
      req: { inputs: { topic: 'robots', tone: 'dry', count: 2, brief: '/Users/me/brief.pdf', deep: true }, test: true },
    });
  });
});

describe('TurnOnDialog', () => {
  it('shows the schedule with its next time, ticks proposed grants, warns when untested, and sets grants before the switch', async () => {
    const proposed: Grant[] = [
      { tool: 'web_fetch', match: { domain: 'acme.com' }, action: 'allow' },
      { tool: 'bash', match: { command: '^ls$' }, action: 'allow' },
    ];
    const bridge = installBridge({
      'automations.validate': () => ({ errors: [], warnings: [], next_times: { '0': ['2026-10-05T06:00:00.000Z'] } }),
      'automations.setGrants': () => automationDetail(),
      'automations.setEnabled': () => automationDetail({ enabled: true }),
    });
    const onDone = vi.fn();
    const onTestFirst = vi.fn();
    const detail = automationDetail({ enabled: false, proposed_grants: proposed, enable_request: { note: 'Tested with robots.', proposed_grants: [proposed[0]!], at: 't' } });
    render(<TurnOnDialog detail={detail} onClose={() => {}} onDone={onDone} onTestFirst={onTestFirst} />);
    expect(screen.getByRole('alert').textContent).toBe("v7 hasn't been tested (v6 was).");
    expect(await screen.findByText(/· next/)).toBeTruthy();
    expect(screen.getByText('Desk: Tested with robots.')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Allow bash matching ^ls$'));
    fireEvent.click(screen.getByRole('button', { name: 'Test first' }));
    expect(onTestFirst).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Turn on anyway' }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(bridge.calls.map((c) => c.channel).filter((c) => c.startsWith('automations.set'))).toEqual(['automations.setGrants', 'automations.setEnabled']);
    expect(bridge.calls.find((c) => c.channel === 'automations.setGrants')?.input).toEqual({ id: 'a1', grants: [proposed[0]], reason: 'enabled' });
  });
});

describe('NameDialog', () => {
  it('explains a bad or taken name and confirms a good one', () => {
    const onConfirm = vi.fn();
    render(<NameDialog title="New automation" confirmLabel="Create" taken={['digest']} onClose={() => {}} onConfirm={onConfirm} />);
    const input = screen.getByLabelText('Name') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'digest' } });
    expect(screen.getByRole('alert').textContent).toBe('Another automation already has this name.');
    fireEvent.change(input, { target: { value: '-bad' } });
    expect(screen.getByRole('alert').textContent).toMatch(/lowercase/i);
    fireEvent.change(input, { target: { value: 'Weekly Note' } });
    expect(input.value).toBe('weekly-note');
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(onConfirm).toHaveBeenCalledWith('weekly-note');
  });
});
