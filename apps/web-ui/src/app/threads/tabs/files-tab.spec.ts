import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { WorkspaceEntry } from '@desk/protocol';
import { FakeDeskBridge, type FakeHandlers } from '../../testing/fake-bridge';
import { FilesTab } from './files-tab';

const listing: Record<string, WorkspaceEntry[]> = {
  '': [
    { name: 'notes.txt', path: 'notes.txt', type: 'file', size: 2048 },
    { name: 'emails', path: 'emails', type: 'dir', size: 0 },
  ],
  emails: [{ name: 'a.md', path: 'emails/a.md', type: 'file', size: 5 }],
};

async function mount(handlers: FakeHandlers) {
  const bridge = new FakeDeskBridge(handlers);
  await render(`<div deskFilesTab threadId="t" [(dir)]="dir" [version]="version"></div>`, {
    imports: [FilesTab],
    componentProperties: { dir: '', version: '1' },
    providers: bridge.providers,
  });
  return bridge;
}

describe('FilesTab', () => {
  it('lists folders first, walks into one and back by its crumbs, and shows the file picked', async () => {
    const bridge = await mount({
      'threads.files': ({ path }: { path?: string }) => listing[path ?? ''] ?? [],
      'threads.file': () => new TextEncoder().encode('# Hi'),
    });
    await screen.findByRole('button', { name: /emails/ });
    expect([...document.querySelectorAll('.files-entry .mono')].map((n) => n.textContent)).toEqual(['emails', 'notes.txt']);
    expect(screen.getByRole('button', { name: /notes\.txt/ }).textContent).toContain('2.0 KB');
    expect(screen.getByText('Pick a file to view it.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /emails/ }));
    fireEvent.click(await screen.findByRole('button', { name: /a\.md/ }));
    expect(await screen.findByRole('heading', { name: 'Hi' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /a\.md/ }).className).toBe('files-entry current');
    expect(screen.getByRole('button', { name: 'Save a copy…' })).toBeTruthy();
    expect(document.querySelector('nav.crumbs')!.textContent).toBe('workspace / emails');
    expect(bridge.calls.map((c) => [c.channel, c.input])).toEqual([
      ['threads.files', { id: 't' }],
      ['threads.files', { id: 't', path: 'emails' }],
      ['threads.file', { id: 't', path: 'emails/a.md' }],
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'workspace' }));
    expect(await screen.findByRole('button', { name: /notes\.txt/ })).toBeTruthy();
    expect(document.querySelector('nav.crumbs')!.textContent).toBe('workspace');
  });

  it('says the workspace is gone once the thread is archived', async () => {
    await mount({
      'threads.files': () => {
        throw { code: 'conflict', message: 'archived', status: 409 };
      },
    });
    expect(await screen.findByRole('heading', { name: 'The workspace is gone' })).toBeTruthy();
    expect(screen.getByText('It was removed when this thread was archived. Published files are still in the Library.')).toBeTruthy();
  });

  it('says an empty folder is empty', async () => {
    await mount({ 'threads.files': () => [] });
    expect(await screen.findByText('This folder is empty.')).toBeTruthy();
    expect(screen.getByText('Pick a file to view it.')).toBeTruthy();
  });

  it('says why a listing failed', async () => {
    await mount({
      'threads.files': () => {
        throw { code: 'internal', message: 'disk on fire', status: 500 };
      },
    });
    expect(await screen.findByRole('heading', { name: "Couldn't list the workspace" })).toBeTruthy();
    expect(screen.getByText('disk on fire')).toBeTruthy();
  });
});
