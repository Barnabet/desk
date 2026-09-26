// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '../../../shared/state';
import { globalStore } from '../../state/global';
import { installBridge } from '../../test/bridge';
import { builtin } from '../../test/builtins';
import { BuiltinGroup } from './BuiltinGroup';
import { BuiltinPanel } from './BuiltinPanel';

afterEach(cleanup);
beforeEach(() => {
  localStorage.clear();
  globalStore.set(initialGlobalState());
});

const pdf = builtin('pdf-toolkit', { title: 'PDF toolkit', caveats: ['Scanned pages are shown as images.'] });
const detail = { name: 'pdf-toolkit', scope: 'builtin', description: 'Read and write PDFs.', instructions: '# PDF toolkit\n\nRun scripts/pdf_read.py.', frontmatter: {}, version: 1, dir: '/app/pdf-toolkit', files: [{ path: 'SKILL.md', size: 40 }, { path: 'scripts/pdf_read.py', size: 12 }] };

describe('Built into Desk', () => {
  it('shows each built-in with its environment and switch, and folds away on the map', async () => {
    const bridge = installBridge({ 'builtins.setEnabled': ({ name, enabled }: { name: string; enabled: boolean }) => builtin(name, { enabled }) });
    const onChanged = vi.fn();
    const onSelect = vi.fn();
    const items = [pdf, builtin('images', { title: 'Images and fonts', broken: 'images does not match' }), builtin('archives', { enabled: false, runtime: { state: 'failed', reason: 'no network' } })];
    const { rerender } = render(<BuiltinGroup items={items} selected="builtin:pdf-toolkit" onSelect={onSelect} onChanged={onChanged} />);
    const group = screen.getByRole('region', { name: 'Built into Desk' });
    expect(group.textContent).toContain('· 3 · 1 off');
    expect(within(group).getByText('Set up on first use')).toBeTruthy();
    expect(within(group).getByText('Damaged: reinstall Desk')).toBeTruthy();
    expect(within(group).getByText('Setup failed: no network')).toBeTruthy();
    expect(within(group).queryByRole('switch', { name: 'Images and fonts' })).toBeNull();
    expect(within(group).getByRole('switch', { name: 'Archives' }).getAttribute('aria-checked')).toBe('false');
    fireEvent.click(within(group).getByRole('switch', { name: 'PDF toolkit' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(bridge.calls.find((c) => c.channel === 'builtins.setEnabled')?.input).toEqual({ name: 'pdf-toolkit', enabled: false });
    fireEvent.click(within(group).getByRole('button', { name: 'Open Archives' }));
    expect(onSelect).toHaveBeenCalledWith('builtin:archives');

    rerender(<BuiltinGroup items={items} selected={null} collapsible onSelect={onSelect} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
    expect(screen.queryByRole('switch')).toBeNull();
    expect(localStorage.getItem('desk.builtinsOpen')).toBe('false');
  });

  it('shows live setup progress', () => {
    globalStore.set({ ...initialGlobalState(), runtimes: { progress: { 'builtin:pdf-toolkit': { step: 'Installing 6 Python packages' } } } } as never);
    render(<BuiltinGroup items={[{ ...pdf, runtime: { state: 'preparing', reason: null } }]} selected={null} onSelect={() => {}} onChanged={() => {}} />);
    expect(screen.getByText('Setting up… Installing 6 Python packages')).toBeTruthy();
  });

  it('reads instructions and files, retries a failed setup, and duplicates into a project', async () => {
    const bridge = installBridge({
      'builtins.get': () => detail,
      'builtins.file': () => new TextEncoder().encode('print("pdf")'),
      'builtins.retry': () => ({ state: 'preparing', reason: null }),
      'builtins.duplicate': () => ({ dir: '/data/projects/p1/skills/pdf-toolkit', created: true, version: 1 }),
    });
    const onDuplicated = vi.fn();
    const onChanged = vi.fn();
    render(<BuiltinPanel item={{ ...pdf, runtime: { state: 'failed', reason: 'no network' } }} projects={[{ id: 'p1', name: 'Tax' }]} onDuplicated={onDuplicated} onChanged={onChanged} onClose={() => {}} />);
    const panel = screen.getByRole('article', { name: 'Built-in skill pdf-toolkit' });
    expect(await within(panel).findByText('Read and write PDFs.')).toBeTruthy();
    expect(panel.textContent).toContain('Scanned pages are shown as images.');
    expect(within(panel).queryByRole('button', { name: 'Edit' })).toBeNull();
    fireEvent.click(within(panel).getByRole('tab', { name: 'Files · 2' }));
    fireEvent.click(within(panel).getByRole('button', { name: /scripts\/pdf_read\.py/ }));
    expect(await within(panel).findByText('print("pdf")')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(bridge.calls.find((c) => c.channel === 'builtins.retry')?.input).toEqual({ name: 'pdf-toolkit' });

    fireEvent.click(within(panel).getByRole('button', { name: 'Duplicate to my skills' }));
    const sheet = screen.getByRole('dialog', { name: 'Duplicate pdf-toolkit' });
    fireEvent.change(within(sheet).getByLabelText('Where'), { target: { value: 'p1' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Duplicate' }));
    await waitFor(() => expect(onDuplicated).toHaveBeenCalledWith({ scope: 'project', projectId: 'p1', name: 'pdf-toolkit' }));
    expect(bridge.calls.find((c) => c.channel === 'builtins.duplicate')?.input).toEqual({ name: 'pdf-toolkit', projectId: 'p1' });
  });

  it('says when your own skill shadows it, and when it is damaged', async () => {
    installBridge({ 'builtins.get': () => detail });
    const { rerender } = render(<BuiltinPanel item={{ ...pdf, shadowed_by: 'global' }} projects={[]} onDuplicated={() => {}} onChanged={() => {}} onClose={() => {}} />);
    expect(screen.getByRole('link', { name: 'pdf-toolkit' }).getAttribute('href')).toBe('#/skills/global%3Apdf-toolkit');
    rerender(<BuiltinPanel item={{ ...pdf, broken: 'pdf-toolkit does not match' }} projects={[]} onDuplicated={() => {}} onChanged={() => {}} onClose={() => {}} />);
    expect(screen.getByRole('alert').textContent).toContain('Reinstall Desk');
    expect(screen.queryByRole('switch')).toBeNull();
  });
});
