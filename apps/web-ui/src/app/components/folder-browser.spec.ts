import { render, screen, waitFor, within } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import type { DirListing } from '@desk/web-server/contract';
import { describe, expect, it } from 'vitest';
import { DeskBridge, type FolderPurpose } from '../core/desk-bridge';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { FolderBrowser, isAbsolutePath } from './folder-browser';

const HOME: DirListing = {
  path: '/Users/me',
  parent: null,
  dirs: [
    { name: 'code', path: '/Users/me/code' },
    { name: 'Documents', path: '/Users/me/Documents' },
  ],
};
const HOME_HIDDEN: DirListing = { ...HOME, dirs: [{ name: '.claude', path: '/Users/me/.claude' }, ...HOME.dirs] };
const CODE: DirListing = { path: '/Users/me/code', parent: '/Users/me', dirs: [{ name: 'app', path: '/Users/me/code/app' }] };

type ListInput = { path?: string; hidden?: boolean };

/** fs.listDirs as desk web answers it (W0b.7): home, ~ expanded, folders outside home refused (~/ext links to one). */
function listDirs(input: ListInput): DirListing {
  const path = input.path === undefined || input.path === '~' ? '/Users/me' : input.path.startsWith('~/') ? `/Users/me/${input.path.slice(2)}` : input.path;
  if (!path.startsWith('/')) throw { code: 'invalid_path', message: 'Type a full path, such as ~/code.', status: 400 };
  if (path === '/Users/me/ext') throw { code: 'not_allowed', message: "Desk can browse your home folder and your projects' sources only.", status: 403 };
  if (path === '/Users/me') return input.hidden ? HOME_HIDDEN : HOME;
  if (path === '/Users/me/code') return CODE;
  if (path === '/Users/me/code/app') return { path, parent: '/Users/me/code', dirs: [] };
  if (path.startsWith('/Users/me/')) throw { code: 'not_found', message: 'That folder does not exist.', status: 404 };
  throw { code: 'not_allowed', message: "Desk can browse your home folder and your projects' sources only.", status: 403 };
}

async function setup(purpose: FolderPurpose = 'source', list: (input: ListInput) => DirListing | Promise<DirListing> = listDirs) {
  const bridge = new FakeDeskBridge({ 'fs.listDirs': list });
  const picked: Array<string | null> = [];
  await render(FolderBrowser, { inputs: { purpose }, providers: [{ provide: DeskBridge, useValue: bridge }], on: { picked: (p: string | null) => picked.push(p) } });
  const lists = () => bridge.calls.filter((c) => c.channel === 'fs.listDirs').map((c) => c.input);
  return { picked, lists, user: userEvent.setup(), input: () => screen.getByLabelText('Folder') as HTMLInputElement };
}

describe('FolderBrowser', () => {
  it('lists the home folder, opens a folder and goes back up', async () => {
    const { lists, user, input } = await setup();
    const dialog = await screen.findByRole('dialog', { name: 'Choose a folder' });
    expect(within(dialog).getByRole('list', { name: 'Folders in /Users/me' })).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: '.claude' })).toBeNull();
    expect((within(dialog).getByRole('button', { name: 'Up' }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(within(dialog).getByRole('button', { name: 'code' }));
    await within(dialog).findByRole('button', { name: 'app' });
    expect(input().value).toBe('/Users/me/code');
    await user.click(within(dialog).getByRole('button', { name: 'Up' }));
    await within(dialog).findByRole('button', { name: 'Documents' });
    expect(input().value).toBe('/Users/me');
    expect(lists()).toEqual([{ hidden: false }, { path: '/Users/me/code', hidden: false }, { path: '/Users/me', hidden: false }]);
  });

  it('chooses the folder it shows, and says when a folder has no folders in it', async () => {
    const { picked, lists, user } = await setup();
    await user.click(await screen.findByRole('button', { name: 'code' }));
    await user.click(await screen.findByRole('button', { name: 'app' }));
    expect(await screen.findByText('No folders here.')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Choose this folder' }));
    expect(picked).toEqual(['/Users/me/code/app']);
    expect(lists()).toHaveLength(3);
  });

  it('goes to a typed path, and says why it cannot list one', async () => {
    const { user, input } = await setup();
    await screen.findByRole('button', { name: 'code' });
    await user.clear(input());
    await user.type(input(), '~/code{Enter}');
    await screen.findByRole('button', { name: 'app' });
    expect(input().value).toBe('/Users/me/code');
    await user.clear(input());
    await user.type(input(), '/etc{Enter}');
    expect((await screen.findByRole('alert')).textContent).toContain("Desk can browse your home folder and your projects' sources only.");
    expect(screen.getByRole('button', { name: 'app' })).toBeTruthy();
    expect(input().value).toBe('/etc');
  });

  it('chooses a typed folder: resolved when it can list it, as typed when deskd must check it, never a relative one', async () => {
    const { picked, user, input } = await setup();
    await screen.findByRole('button', { name: 'code' });
    await user.clear(input());
    await user.type(input(), '~/code');
    await user.click(screen.getByRole('button', { name: 'Choose this folder' }));
    await waitFor(() => expect(picked).toEqual(['/Users/me/code']));
    await user.clear(input());
    await user.type(input(), '/Volumes/work/repo');
    await user.click(screen.getByRole('button', { name: 'Choose this folder' }));
    await waitFor(() => expect(picked).toEqual(['/Users/me/code', '/Volumes/work/repo']));
    await user.clear(input());
    await user.type(input(), 'code');
    await user.click(screen.getByRole('button', { name: 'Choose this folder' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Type a full path');
    await user.clear(input());
    await user.type(input(), '/Users/me/missing');
    await user.click(screen.getByRole('button', { name: 'Choose this folder' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('That folder does not exist.'));
    expect(picked).toEqual(['/Users/me/code', '/Volumes/work/repo']);
  });

  it('never hands deskd a ~ path, even one the browser may not list (~/ext, a link to a folder outside home)', async () => {
    const { picked, user, input } = await setup();
    await screen.findByRole('button', { name: 'code' });
    await user.clear(input());
    await user.type(input(), '~/ext');
    await user.click(screen.getByRole('button', { name: 'Choose this folder' }));
    expect((await screen.findByRole('alert')).textContent).toContain("Desk can browse your home folder and your projects' sources only.");
    expect(picked).toEqual([]);
  });

  it('keeps Choose this folder pending while a listing lands or a typed folder is checked: no stale folder, no second answer', async () => {
    const waiting: Array<() => void> = [];
    const answerLater = (i: ListInput) =>
      new Promise<DirListing>((resolve, reject) =>
        waiting.push(() => {
          try {
            resolve(listDirs(i));
          } catch (err) {
            reject(err);
          }
        }),
      );
    const { picked, lists, user, input } = await setup('source', answerLater);
    const choose = (await screen.findByRole('button', { name: 'Choose this folder' })) as HTMLButtonElement;
    expect(choose.disabled).toBe(true);
    waiting.shift()!();
    await user.click(await screen.findByRole('button', { name: 'code' }));
    await waitFor(() => expect(choose.getAttribute('aria-busy')).toBe('true'));
    await user.click(choose);
    expect(picked).toEqual([]);
    waiting.shift()!();
    await screen.findByRole('button', { name: 'app' });
    await waitFor(() => expect(choose.disabled).toBe(false));
    await user.clear(input());
    await user.type(input(), '/Volumes/work/repo');
    await user.click(choose);
    await waitFor(() => expect(choose.getAttribute('aria-busy')).toBe('true'));
    await user.click(choose);
    expect(waiting).toHaveLength(1);
    waiting.shift()!();
    await waitFor(() => expect(picked).toEqual(['/Volumes/work/repo']));
    expect(lists()).toHaveLength(3);
  });

  it('shows hidden folders when asked, and from the start for a skill import', async () => {
    const { lists, user } = await setup('skill-import');
    expect(await screen.findByRole('dialog', { name: 'Choose a skill folder' })).toBeTruthy();
    await screen.findByRole('button', { name: '.claude' });
    const hidden = screen.getByRole('checkbox', { name: 'Show hidden folders' }) as HTMLInputElement;
    expect(hidden.checked).toBe(true);
    await user.click(hidden);
    await waitFor(() => expect(screen.queryByRole('button', { name: '.claude' })).toBeNull());
    expect(lists()).toEqual([{ hidden: true }, { path: '/Users/me', hidden: false }]);
  });

  it('chooses nothing on Cancel or Escape', async () => {
    const { picked, user } = await setup();
    await screen.findByRole('button', { name: 'code' });
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await user.keyboard('{Escape}');
    expect(picked).toEqual([null, null]);
  });
});

describe('isAbsolutePath', () => {
  it('knows full paths on macOS, Linux and Windows', () => {
    for (const p of ['/Users/me', 'C:\\Users\\me', 'd:/work', '\\\\server\\share']) expect(isAbsolutePath(p), p).toBe(true);
    for (const p of ['code', '~/code', './code', '']) expect(isAbsolutePath(p), p).toBe(false);
  });
});
