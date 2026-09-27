import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { VersionsView } from './versions-view';

const older = () => ({ ...digestDef(), steps: digestDef().steps.slice(0, 2), edges: [digestDef().edges[0]!] });

describe('VersionsView', () => {
  it('lists versions with who saved them, compares two, and restores one', async () => {
    const bridge = new FakeDeskBridge({
      'automations.versions': () => [
        { version: 7, origin: 'agent:d1', change_note: 'Adds a publish step', via: 'tool', created_at: '2026-09-27T09:00:00.000Z', tested: false },
        { version: 6, origin: 'user', change_note: '', via: 'editor', created_at: '2026-09-26T09:00:00.000Z', tested: true },
      ],
      'automations.version': ({ version }: { version: number }) => ({ version, definition: version === 7 ? digestDef() : older(), origin: 'user', change_note: '', via: 'editor', created_at: 't' }),
      'automations.restore': () => automationDetail({ version: 8 }),
    });
    const onChange = vi.fn();
    await render(VersionsView, { inputs: { detail: automationDetail() }, on: { detailChange: onChange }, providers: bridge.providers });
    const v7 = (await screen.findByText('v7', { selector: 'b' })).closest('li')!;
    expect(v7.textContent).toContain('Desk');
    expect(v7.textContent).toContain('Adds a publish step');
    expect(v7.textContent).toContain('current');
    const v6 = screen.getByText('v6', { selector: 'b' }).closest('li')!;
    expect(v6.textContent).toContain('You');
    expect(v6.textContent).toContain('✓ tested');
    expect((screen.getByLabelText('Compare') as HTMLSelectElement).value).toBe('6');
    expect((screen.getByLabelText('with') as HTMLSelectElement).value).toBe('7');
    const diff = await screen.findByRole('region', { name: 'Changes from v6 to v7' });
    // The region shows at once; its diff arrives once both versions have loaded.
    expect(await within(diff).findByText('Publish?')).toBeTruthy();
    expect(within(diff).getByText('Summarise → Publish?')).toBeTruthy();

    fireEvent.click(within(v6).getByRole('button', { name: 'Restore…' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Restore v6?' })).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ version: 8 })));
    expect(bridge.calls.find((c) => c.channel === 'automations.restore')?.input).toEqual({ id: 'a1', version: 6 });
    expect(bridge.calls.filter((c) => c.channel === 'automations.version').map((c) => (c.input as { version: number }).version).sort()).toEqual([6, 7]);
  });
});
