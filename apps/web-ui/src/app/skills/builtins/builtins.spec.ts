import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { ToastService } from '../../components/toast';
import { builtin } from '../../testing/builtins';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../../testing/fake-bridge';
import { BuiltinGroup } from './builtin-group';
import { BuiltinPanel } from './builtin-panel';

beforeEach(() => {
  localStorage.clear();
});

const pdf = builtin('pdf-toolkit', { title: 'PDF toolkit', caveats: ['Scanned pages are shown as images.'] });
const detail = {
  name: 'pdf-toolkit',
  scope: 'builtin',
  description: 'Read and write PDFs.',
  instructions: '# PDF toolkit\n\nRun scripts/pdf_read.py.',
  frontmatter: {},
  version: 1,
  dir: '/app/pdf-toolkit',
  files: [
    { path: 'SKILL.md', size: 40 },
    { path: 'scripts/pdf_read.py', size: 12 },
  ],
};
const failed = { state: 'failed', reason: 'no network' } as const;
const copy = { dir: '/data/skills/pdf-toolkit', created: true, version: 1 };

async function group(o: { items: BuiltinSkillInfo[]; selected?: string | null; collapsible?: boolean; handlers?: FakeHandlers; progress?: Record<string, { step: string }> }) {
  const bridge = new FakeDeskBridge(o.handlers ?? {});
  const outputs = { selectSkill: vi.fn(), changed: vi.fn() };
  const view = await render(BuiltinGroup, {
    inputs: { items: o.items, selected: o.selected ?? null, collapsible: o.collapsible ?? false },
    on: outputs,
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), runtimes: { progress: o.progress ?? {}, seq: 0 } })],
    // The host must be the <section> its selector names, or getByRole('region') finds nothing.
    configureTestBed: (testBed) => testBed.configureTestingModule({ inferTagName: true }),
  });
  return { bridge, view, outputs };
}

async function panel(item: BuiltinSkillInfo, handlers: FakeHandlers = {}, projects: Array<{ id: string; name: string }> = []) {
  const bridge = new FakeDeskBridge({ 'builtins.get': () => detail, ...handlers });
  const outputs = { duplicated: vi.fn(), changed: vi.fn(), close: vi.fn() };
  const view = await render(BuiltinPanel, {
    inputs: { item, projects },
    on: outputs,
    providers: [...bridge.providers, provideGlobal(initialGlobalState())],
    configureTestBed: (testBed) => testBed.configureTestingModule({ inferTagName: true }),
  });
  return { bridge, view, outputs };
}

/** Another built-in opens in the same panel; `on` again, since rerender drops the output listeners it is not given. */
async function open(view: Awaited<ReturnType<typeof panel>>['view'], outputs: Awaited<ReturnType<typeof panel>>['outputs'], item: BuiltinSkillInfo) {
  await view.rerender({ inputs: { item }, on: outputs, partialUpdate: true });
  await waitFor(() => expect(screen.getByRole('article').getAttribute('aria-label')).toBe(`Built-in skill ${item.name}`));
}

describe('Built into Desk (Builtins.test.tsx)', () => {
  it('shows each built-in with its environment and switch, and folds away on the map', async () => {
    const items = [pdf, builtin('images', { title: 'Images and fonts', broken: 'images does not match' }), builtin('archives', { enabled: false, runtime: failed })];
    const { bridge, view, outputs } = await group({
      items,
      selected: 'builtin:pdf-toolkit',
      handlers: { 'builtins.setEnabled': ({ name, enabled }: { name: string; enabled: boolean }) => builtin(name, { enabled }) },
    });
    const region = screen.getByRole('region', { name: 'Built into Desk' });
    expect(region.textContent).toContain('· 3 · 1 off');
    expect(within(region).getByText('Set up on first use')).toBeTruthy();
    expect(within(region).getByText('Damaged: reinstall Desk')).toBeTruthy();
    expect(within(region).getByText('Setup failed: no network')).toBeTruthy();
    expect([...region.querySelectorAll('.runtime-line')].map((l) => l.className)).toEqual(['runtime-line none', 'runtime-line failed', 'runtime-line failed']);
    expect(within(region).queryByRole('switch', { name: 'Images and fonts' })).toBeNull();
    expect(within(region).getByRole('switch', { name: 'Archives' }).getAttribute('aria-checked')).toBe('false');
    expect(region.querySelectorAll('.builtin-card.selected')).toHaveLength(1);
    expect([...region.querySelectorAll('.builtin-card')].map((c) => c.classList.contains('off'))).toEqual([false, true, true]);
    fireEvent.click(within(region).getByRole('switch', { name: 'PDF toolkit' }));
    await waitFor(() => expect(outputs.changed).toHaveBeenCalled());
    expect(bridge.calls.find((c) => c.channel === 'builtins.setEnabled')?.input).toEqual({ name: 'pdf-toolkit', enabled: false });
    fireEvent.click(within(region).getByRole('button', { name: 'Open Archives' }));
    expect(outputs.selectSkill).toHaveBeenCalledWith('builtin:archives');

    await view.rerender({ inputs: { selected: null, collapsible: true }, partialUpdate: true });
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
    await waitFor(() => expect(screen.queryByRole('switch')).toBeNull());
    expect(screen.getByRole('button', { name: 'Show' }).getAttribute('aria-expanded')).toBe('false');
    expect(localStorage.getItem('desk.builtinsOpen')).toBe('false');
  });

  it('shows live setup progress', async () => {
    await group({ items: [{ ...pdf, runtime: { state: 'preparing', reason: null } }], progress: { 'builtin:pdf-toolkit': { step: 'Installing 6 Python packages' } } });
    expect(screen.getByText('Setting up… Installing 6 Python packages')).toBeTruthy();
  });

  it('reads instructions and files, retries a failed setup, and duplicates into a project', async () => {
    const { bridge, outputs } = await panel(
      { ...pdf, runtime: failed },
      {
        'builtins.file': () => new TextEncoder().encode('print("pdf")'),
        'builtins.retry': () => ({ state: 'preparing', reason: null }),
        'builtins.duplicate': () => ({ dir: '/data/projects/p1/skills/pdf-toolkit', created: true, version: 1 }),
      },
      [{ id: 'p1', name: 'Tax' }],
    );
    const article = screen.getByRole('article', { name: 'Built-in skill pdf-toolkit' });
    expect(await within(article).findByText('Read and write PDFs.')).toBeTruthy();
    expect(article.textContent).toContain('Scanned pages are shown as images.');
    expect(within(article).queryByRole('button', { name: 'Edit' })).toBeNull();
    fireEvent.click(within(article).getByRole('tab', { name: 'Files · 2' }));
    fireEvent.click(await within(article).findByRole('button', { name: /scripts\/pdf_read\.py/ }));
    expect(await within(article).findByText('print("pdf")')).toBeTruthy();
    fireEvent.click(within(article).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(outputs.changed).toHaveBeenCalled());
    expect(bridge.calls.find((c) => c.channel === 'builtins.retry')?.input).toEqual({ name: 'pdf-toolkit' });

    fireEvent.click(within(article).getByRole('button', { name: 'Duplicate to my skills' }));
    const sheet = await screen.findByRole('dialog', { name: 'Duplicate pdf-toolkit' });
    fireEvent.change(within(sheet).getByLabelText('Where'), { target: { value: 'p1' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Duplicate' }));
    await waitFor(() => expect(outputs.duplicated).toHaveBeenCalledWith({ scope: 'project', projectId: 'p1', name: 'pdf-toolkit' }));
    expect(bridge.calls.find((c) => c.channel === 'builtins.duplicate')?.input).toEqual({ name: 'pdf-toolkit', projectId: 'p1' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('says when your own skill shadows it, and when it is damaged', async () => {
    const { view } = await panel({ ...pdf, shadowed_by: 'global' });
    expect(screen.getByRole('link', { name: 'pdf-toolkit' }).getAttribute('href')).toBe('#/skills/global%3Apdf-toolkit');
    await view.rerender({ inputs: { item: { ...pdf, broken: 'pdf-toolkit does not match' } }, partialUpdate: true });
    expect(screen.getByRole('alert').textContent).toContain('Reinstall Desk');
    expect(screen.queryByRole('switch')).toBeNull();
  });
});

describe('BuiltinPanel, beyond the React cases', () => {
  it('fetches each built-in once, even when the call reads a signal that changes later', async () => {
    // The real bridge can read its signedOut signal inside call() (a sign-in retry); the fetch must not track it.
    const signedIn = signal(true);
    const { bridge } = await panel(pdf, {
      'builtins.get': () => {
        signedIn();
        return detail;
      },
    });
    await within(screen.getByRole('article')).findByText('Read and write PDFs.');
    signedIn.set(false);
    TestBed.tick();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(bridge.calls.filter((c) => c.channel === 'builtins.get')).toHaveLength(1);
  });

  it('drops a file that lands after another built-in opened', async () => {
    let release: (data: Uint8Array) => void = () => {};
    const { view, outputs } = await panel(pdf, { 'builtins.file': () => new Promise<Uint8Array>((resolve) => (release = resolve)) });
    const article = screen.getByRole('article');
    fireEvent.click(await within(article).findByRole('tab', { name: 'Files · 2' }));
    fireEvent.click(within(article).getByRole('button', { name: /scripts\/pdf_read\.py/ }));
    await open(view, outputs, builtin('archives'));
    release(new TextEncoder().encode('print("pdf")'));
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(within(article).getByRole('tab', { name: 'Files · 2' }));
    expect(within(article).getByText('Pick a file to view it.')).toBeTruthy();
    expect(within(article).queryByText('print("pdf")')).toBeNull();
  });

  it("keeps each built-in's running Retry and switch to itself, however the user moves between them", async () => {
    const held: Array<() => void> = [];
    function hold<T>(value: T): Promise<T> {
      return new Promise<T>((resolve) => held.push(() => resolve(value)));
    }
    const { view, outputs } = await panel(
      { ...pdf, runtime: failed },
      {
        'builtins.retry': () => hold({ state: 'preparing', reason: null }),
        'builtins.setEnabled': ({ name }: { name: string }) => hold(builtin(name, { enabled: false })),
      },
    );
    const article = screen.getByRole('article');
    const retryBusy = () => within(article).getByRole('button', { name: 'Retry' }).getAttribute('aria-busy');
    const switchOff = () => (within(article).getByRole('switch') as HTMLButtonElement).disabled;
    fireEvent.click(within(article).getByRole('button', { name: 'Retry' }));
    fireEvent.click(within(article).getByRole('switch'));
    expect([retryBusy(), switchOff()]).toEqual(['true', true]);
    await open(view, outputs, builtin('archives', { runtime: failed }));
    expect([retryBusy(), switchOff()]).toEqual([null, false]);
    await open(view, outputs, { ...pdf, runtime: failed });
    expect([retryBusy(), switchOff()]).toEqual(['true', true]);
    held.forEach((release) => release());
    await waitFor(() => expect(outputs.changed).toHaveBeenCalledTimes(2));
    await waitFor(() => expect([retryBusy(), switchOff()]).toEqual([null, false]));
  });

  it('keeps the duplicate sheet open while the copy is made, then opens the copy', async () => {
    let release: (r: typeof copy) => void = () => {};
    const { outputs } = await panel(pdf, { 'builtins.duplicate': () => new Promise<typeof copy>((resolve) => (release = resolve)) });
    fireEvent.click(within(screen.getByRole('article')).getByRole('button', { name: 'Duplicate to my skills' }));
    const sheet = await screen.findByRole('dialog', { name: 'Duplicate pdf-toolkit' });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Duplicate' }));
    const cancel = within(sheet).getByRole('button', { name: 'Cancel' }) as HTMLButtonElement;
    await waitFor(() => expect(cancel.disabled).toBe(true));
    fireEvent.click(cancel);
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.mouseDown(sheet.parentElement!);
    expect(screen.getByRole('dialog', { name: 'Duplicate pdf-toolkit' })).toBeTruthy();
    release(copy);
    await waitFor(() => expect(outputs.duplicated).toHaveBeenCalledWith({ scope: 'global', name: 'pdf-toolkit' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('closes the duplicate sheet when another built-in opens, and a late copy only refreshes the lists', async () => {
    let release: (r: typeof copy) => void = () => {};
    const { view, outputs } = await panel(pdf, { 'builtins.duplicate': () => new Promise<typeof copy>((resolve) => (release = resolve)) });
    fireEvent.click(within(screen.getByRole('article')).getByRole('button', { name: 'Duplicate to my skills' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Duplicate pdf-toolkit' })).getByRole('button', { name: 'Duplicate' }));
    await open(view, outputs, builtin('archives'));
    expect(screen.queryByRole('dialog')).toBeNull();
    release(copy);
    await waitFor(() => expect(outputs.changed).toHaveBeenCalledTimes(1));
    expect(outputs.duplicated).not.toHaveBeenCalled();
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Duplicated pdf-toolkit. Your copy is used instead of the built-in.']);
  });
});
