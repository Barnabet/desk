import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import { ToastService } from '../components/toast';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { ImportSheet } from './import-sheet';

async function setup(handlers: FakeHandlers) {
  const bridge = new FakeDeskBridge(handlers);
  const done = vi.fn();
  const closed = vi.fn();
  await render(ImportSheet, {
    inputs: { path: '/Users/me/.claude/skills/pdf', projects: [{ id: 'p1', name: 'Onboarding' }, { id: 'p2', name: 'Tax' }] },
    on: { done, close: closed },
    providers: [...bridge.providers, provideGlobal(initialGlobalState())],
  });
  return { bridge, done, closed, sheet: await screen.findByRole('dialog', { name: 'Import a skill' }) };
}

describe('ImportSheet', () => {
  it('imports the folder into a project under a new name, and names the skill it became', async () => {
    const { bridge, done, sheet } = await setup({ 'skills.import': () => ({ version: 1, dir: '/data/projects/p2/skills/pdf-notes', created: true, description: '' }) });
    expect(sheet.querySelector('.sheet-body p.small')?.textContent).toBe("From /Users/me/.claude/skills/pdf. The folder needs a SKILL.md. It's copied in; the original stays where it is.");
    expect(within(sheet).getByText("Defaults to the folder's name.")).toBeTruthy();
    expect([...(within(sheet).getByLabelText('Scope') as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(['Global (every project)', 'Onboarding only', 'Tax only']);
    fireEvent.change(within(sheet).getByLabelText('Scope'), { target: { value: 'p2' } });
    fireEvent.input(within(sheet).getByLabelText('Name (optional)'), { target: { value: ' pdf-notes ' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(done).toHaveBeenCalledWith({ scope: 'project', projectId: 'p2', name: 'pdf-notes' }));
    expect(bridge.calls).toEqual([{ channel: 'skills.import', input: { projectId: 'p2', path: '/Users/me/.claude/skills/pdf', name: 'pdf-notes' } }]);
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Imported pdf-notes.']);
  });

  it('names the skill from a Windows path too, and keeps the sheet open when deskd refuses the folder', async () => {
    let refuse = true;
    const { done, sheet } = await setup({
      'skills.import': () => {
        if (refuse) throw { code: 'invalid', message: '/Users/me/.claude/skills/pdf has no SKILL.md', status: 400 };
        return { version: 1, dir: 'C:\\Users\\me\\AppData\\Desk\\skills\\pdf', created: true, description: '' };
      },
    });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['/Users/me/.claude/skills/pdf has no SKILL.md']));
    expect(done).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Import a skill' })).toBeTruthy();
    refuse = false;
    fireEvent.click(within(sheet).getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(done).toHaveBeenCalledWith({ scope: 'global', name: 'pdf' }));
  });

  it('stays open while the import runs, so the imported skill still reaches the screen', async () => {
    let release: (r: { version: number; dir: string; created: boolean; description: string }) => void = () => {};
    const { done, closed, sheet } = await setup({ 'skills.import': () => new Promise((resolve) => (release = resolve)) });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Import' }));
    const cancel = within(sheet).getByRole('button', { name: 'Cancel' }) as HTMLButtonElement;
    await waitFor(() => expect(cancel.disabled).toBe(true));
    fireEvent.click(cancel);
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.mouseDown(sheet.parentElement!);
    expect(closed).not.toHaveBeenCalled();
    release({ version: 1, dir: '/data/skills/pdf', created: true, description: '' });
    await waitFor(() => expect(done).toHaveBeenCalledWith({ scope: 'global', name: 'pdf' }));
    expect(closed).not.toHaveBeenCalled();
  });
});
