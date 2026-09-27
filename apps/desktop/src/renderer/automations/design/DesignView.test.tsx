// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { installReactFlowShims } from '../../test/reactflow';
import { DesignView } from './DesignView';

beforeAll(installReactFlowShims);
beforeEach(() => void (window.location.hash = '#/p/p/automations/a1'));
afterEach(cleanup);

type Req = { req: { definition: AutomationDefinition; base_version?: number; name?: string } };
const valid = () => ({ errors: [], warnings: [], next_times: {} });
const common = { 'skills.list': () => [], 'builtins.list': () => [], 'automations.layout': () => ({ ok: true }) };

describe('DesignView', () => {
  it('marks problems on their node, blocks Save, and shows them in the step inspector', async () => {
    installBridge({ ...common, 'automations.validate': () => ({ errors: [{ path: 'steps[2].question', message: 'Too small' }], warnings: [], next_times: {} }) });
    render(<DesignView projectId="p" sources={[]} detail={automationDetail()} onChange={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('node-ok').className).toContain('invalid'));
    expect(screen.getByRole('status').textContent).toBe('1 problem');
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId('node-ok'));
    expect(screen.getByText('Ask me step')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('question: Too small');
  });

  it('adds a step from the strip, edits it and saves with a note on the current version', async () => {
    const onChange = vi.fn();
    const bridge = installBridge({ ...common, 'automations.validate': valid, 'automations.save': ({ req }: Req) => ({ automation: automationDetail({ version: 8, definition: req.definition }), warnings: [] }) });
    render(<DesignView projectId="p" sources={[]} detail={automationDetail()} onChange={onChange} />);
    await screen.findByText('Saved as v7');
    fireEvent.click(within(screen.getByRole('toolbar', { name: 'Add a step' })).getByRole('button', { name: 'Tell Desk' }));
    expect(await screen.findByTestId('node-tell_desk')).toBeTruthy();
    expect(screen.getByText('Tell Desk step')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Message to Desk'), { target: { value: 'Done.' } });
    fireEvent.change(screen.getByLabelText('Change note'), { target: { value: 'Tells Desk' } });
    await screen.findByText('Ready to save');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ version: 8 })));
    expect(bridge.calls.find((c) => c.channel === 'automations.save')?.input).toMatchObject({ id: 'a1', req: { base_version: 7, change_note: 'Tells Desk', via: 'editor' } });
    await screen.findByText('Saved as v8');
  });

  it('on a 409 shows who saved, the changes, and saves mine on the new base', async () => {
    const onChange = vi.fn();
    const theirs = automationDetail({ version: 8, definition: { ...digestDef(), title: 'Digest (Desk)' } });
    const bridge = installBridge({
      ...common,
      'automations.validate': valid,
      'automations.save': ({ req }: Req) => {
        if (req.base_version === 7) throw { code: 'conflict', message: 'stale', status: 409 };
        return { automation: automationDetail({ version: 9, definition: req.definition }), warnings: [] };
      },
      'automations.get': () => theirs,
      'automations.versions': () => [{ version: 8, origin: 'agent:d1', change_note: 'x', via: 'tool', created_at: '2026-09-27T09:00:00.000Z', tested: false }],
    });
    render(<DesignView projectId="p" sources={[]} detail={automationDetail()} onChange={onChange} />);
    await screen.findByText('Saved as v7');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'My digest' } });
    await screen.findByText('Ready to save');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const dialog = await screen.findByRole('dialog', { name: 'Desk saved v8 while you were editing' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Review changes' }));
    const changes = within(dialog).getByRole('region', { name: 'What saving yours changes' });
    expect(changes.textContent).toContain('Digest (Desk)');
    expect(changes.textContent).toContain('My digest');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save mine anyway' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ version: 9 })));
    expect(bridge.calls.filter((c) => c.channel === 'automations.save').map((c) => (c.input as Req).req.base_version)).toEqual([7, 8]);
  });

  it('saves the layout on its own after Tidy up', async () => {
    const bridge = installBridge({ ...common, 'automations.validate': valid });
    render(<DesignView projectId="p" sources={[]} detail={automationDetail()} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Tidy up' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.layout')?.input).toMatchObject({ id: 'a1', layout: { __start: expect.any(Object), fetch: expect.any(Object) } }), { timeout: 2000 });
  });

  it('creates a Blank automation on its first save, with its layout, and opens it', async () => {
    const bridge = installBridge({
      ...common,
      'automations.validate': ({ req }: Req) => (req.definition.steps.length ? valid() : { errors: [{ path: 'steps', message: 'Add at least one step' }], warnings: [], next_times: {} }),
      'automations.create': ({ req }: Req) => ({ automation: automationDetail({ id: 'a9', name: req.name!, title: 'Weekly note' }), warnings: [] }),
    });
    render(<DesignView projectId="p" sources={[]} draftName="weekly-note" />);
    expect(await screen.findByText('1 problem')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('Add at least one step');
    fireEvent.click(within(screen.getByRole('toolbar', { name: 'Add a step' })).getByRole('button', { name: 'Wait' }));
    await screen.findByText('Ready to save');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/automations/a9'));
    expect(bridge.calls.find((c) => c.channel === 'automations.create')?.input).toMatchObject({
      projectId: 'p',
      req: { name: 'weekly-note', via: 'editor', definition: { title: 'Weekly note', steps: [expect.objectContaining({ kind: 'wait', minutes: 60 })] } },
    });
    expect(bridge.calls.some((c) => c.channel === 'automations.layout' && (c.input as { id: string }).id === 'a9')).toBe(true);
  });
});
