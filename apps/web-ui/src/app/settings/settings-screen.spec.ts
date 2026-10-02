import { Component, inject } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import { DEFAULT_POLICY, RISKY_COMMAND_PATTERN, type StoredEvent } from '@desk/protocol';
import type { DirListing } from '@desk/web-server/contract';
import { FolderBrowser } from '../components/folder-browser';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { screenFor } from '../screen-for';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { SettingsScreen } from './settings-screen';

beforeEach(() => {
  window.location.hash = '#/p/p/settings';
});

const settings = { desk_model: 'claude-opus-5-5', thread_model: 'claude-opus-5-5', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, review_model: null, policy: DEFAULT_POLICY };
const overview = () =>
  ({
    project: { id: 'p', name: 'Tax 2026', goal: 'File on time', instructions: '', settings, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [{ id: 's1', project_id: 'p', path: '/Users/me/tax', kind: 'folder', label: 'tax', agent_write: true, created_at: 't' }],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;
const levels: Record<string, { reasoning_efforts: string[]; default_reasoning_effort: string | null }> = {
  'claude-opus-5-5': { reasoning_efforts: ['low', 'medium', 'high', 'xhigh', 'max'], default_reasoning_effort: 'medium' },
  'claude-fable-5-1': { reasoning_efforts: [], default_reasoning_effort: null },
  'gpt-6-sol': { reasoning_efforts: ['low', 'high'], default_reasoning_effort: null },
};
const models = Object.entries(levels).map(([id, l]) => ({ id, family: 'claude', context_window: 1, max_output_tokens: 1, ...l, concurrency: 1 }));

/** fs.listDirs over a home with one folder in it, as desk web answers it (W0b.7). */
const HOME: DirListing = { path: '/Users/me', parent: null, dirs: [{ name: 'code', path: '/Users/me/code' }] };
const CODE: DirListing = { path: '/Users/me/code', parent: '/Users/me', dirs: [] };
const listDirs = (input: { path?: string }): DirListing => (input.path === '/Users/me/code' ? CODE : HOME);

/** SettingsScreen with the folder browser beside it, as App shows it over every screen while app.pickFolder is open (W0d.7). */
@Component({
  selector: 'desk-with-folders',
  imports: [SettingsScreen, FolderBrowser],
  template: `<div deskSettingsScreen projectId="p"></div>
    @if (bridge.folderRequest(); as request) {<div deskFolderBrowser [purpose]="request.purpose" (picked)="bridge.answerFolder($event)"></div>}`,
})
class WithFolderBrowser {
  protected readonly bridge = inject(DeskBridge);
}

async function setup(extra: FakeHandlers = {}, events: StoredEvent[] = [], withFolders = false) {
  const bridge: FakeDeskBridge = new FakeDeskBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    'models.list': () => models,
    'projects.update': () => ({}),
    ...extra,
  });
  const providers = [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } }), { provide: SESSION_RELEASE_DELAY, useValue: 0 }];
  if (withFolders) await render(WithFolderBrowser, { providers });
  else await render(SettingsScreen, { inputs: { projectId: 'p' }, providers });
  return bridge;
}

const updates = (bridge: FakeDeskBridge) => bridge.calls.filter((c) => c.channel === 'projects.update').map((c) => c.input);

describe('SettingsScreen', () => {
  it('saves the name and goal, and how Desk works', async () => {
    const bridge = await setup();
    fireEvent.input(await screen.findByLabelText('Goal'), { target: { value: 'File by April' } });
    const about = screen.getByRole('region', { name: 'About this project' });
    fireEvent.click(within(about).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updates(bridge)[0]).toEqual({ id: 'p', patch: { name: 'Tax 2026', goal: 'File by April', instructions: '' } }));

    const style = screen.getByRole('region', { name: 'How Desk works' });
    fireEvent.click(within(style).getByLabelText(/Detailed/));
    fireEvent.click(within(style).getByLabelText(/Ask before dispatching/));
    await waitFor(() => expect((within(style).getByLabelText("Threads' model") as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(within(style).getByLabelText('Fallback when rate limited'), { target: { value: 'claude-fable-5-1' } });
    fireEvent.click(within(style).getByRole('button', { name: '6 threads at once' }));
    // Reasoning levels follow the chosen model; a model change drops a level the new model does not take.
    const deskEffort = within(style).getByLabelText("Desk's reasoning effort") as HTMLSelectElement;
    expect([...deskEffort.options].map((o) => o.textContent)).toEqual(['Model default (medium)', 'low', 'medium', 'high', 'xhigh', 'max']);
    fireEvent.change(within(style).getByLabelText("Threads' reasoning effort"), { target: { value: 'xhigh' } });
    fireEvent.change(within(style).getByLabelText("Threads' model"), { target: { value: 'gpt-6-sol' } });
    expect((within(style).getByLabelText("Threads' reasoning effort") as HTMLSelectElement).value).toBe('');
    fireEvent.change(within(style).getByLabelText("Threads' model"), { target: { value: 'claude-fable-5-1' } });
    expect((within(style).getByLabelText("Threads' reasoning effort") as HTMLSelectElement).disabled).toBe(true);
    expect(style.textContent).toContain('claude-fable-5-1 takes no reasoning level');
    fireEvent.change(within(style).getByLabelText("Threads' model"), { target: { value: 'claude-opus-5-5' } });
    fireEvent.change(within(style).getByLabelText("Threads' reasoning effort"), { target: { value: 'xhigh' } });
    fireEvent.click(within(style).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(updates(bridge)[1]).toEqual({
        id: 'p',
        patch: {
          settings: {
            desk_model: 'claude-opus-5-5',
            thread_model: 'claude-opus-5-5',
            fallback_model: 'claude-fable-5-1',
            desk_reasoning_effort: null,
            thread_reasoning_effort: 'xhigh',
            max_concurrent_threads: 6,
            check_in: 'detailed',
            autonomy: 'ask-before-dispatch',
            review_rounds: 2,
          },
        },
      }),
    );
  });

  it('edits the policy in order, resets to the default, and shows the risky pattern by name', async () => {
    const bridge = await setup();
    const policy = await screen.findByRole('region', { name: 'Policy' });
    expect(within(policy).getAllByText('risky commands')).toHaveLength(4);
    expect(within(policy).getByText('This is the default policy.')).toBeTruthy();
    fireEvent.click(within(policy).getByRole('button', { name: 'Add rule' }));
    fireEvent.input(within(policy).getByLabelText('Rule 10 tool'), { target: { value: 'web_fetch' } });
    fireEvent.change(within(policy).getByLabelText('Rule 10 match'), { target: { value: 'domain' } });
    fireEvent.input(within(policy).getByLabelText('Rule 10 pattern'), { target: { value: '*.internal' } });
    fireEvent.change(within(policy).getByLabelText('Rule 10 action'), { target: { value: 'deny' } });
    for (let i = 10; i > 1; i--) fireEvent.click(within(policy).getByRole('button', { name: `Move rule ${i} up` }));
    fireEvent.click(within(policy).getByRole('button', { name: 'Save policy' }));
    await waitFor(() => expect(updates(bridge)).toHaveLength(1));
    const saved = (updates(bridge)[0] as { patch: { settings: { policy: unknown[] } } }).patch.settings.policy;
    expect(saved[0]).toEqual({ tool: 'web_fetch', match: { domain: '*.internal' }, action: 'deny' });
    expect(saved[1]).toEqual({ tool: 'bash', match: { command: RISKY_COMMAND_PATTERN }, action: 'ask' });
    fireEvent.click(within(policy).getByRole('button', { name: 'Reset to the default policy' }));
    expect(within(policy).getByText('This is the default policy.')).toBeTruthy();
  });

  it('adds and removes sources, and archives the project after confirming', async () => {
    const bridge = await setup({ 'app.pickFolder': () => '/Users/me/repo', 'projects.addSource': () => ({}), 'projects.removeSource': () => ({ ok: true }), 'projects.setSourceWrite': () => ({}), 'projects.archive': () => ({ ok: true }) });
    const sources = await screen.findByRole('region', { name: 'Sources' });
    const write = within(sources).getByLabelText('Agents can write here') as HTMLInputElement;
    expect(write.checked).toBe(true);
    fireEvent.click(write);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.setSourceWrite')?.input).toEqual({ id: 'p', sourceId: 's1', agentWrite: false }));
    fireEvent.click(within(sources).getByRole('button', { name: 'Add folder…' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.addSource')?.input).toEqual({ id: 'p', source: { path: '/Users/me/repo' } }));
    fireEvent.click(within(sources).getByRole('button', { name: 'Remove tax' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.removeSource')?.input).toEqual({ id: 'p', sourceId: 's1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive project…' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
  });

  it('follows the live project after a save', async () => {
    const bridge = await setup({}, [ev(9, 'project.updated', { goal: 'File by April' })]);
    await waitFor(() => expect((screen.getByLabelText('Goal') as HTMLTextAreaElement).value).toBe('File by April'));
    // A real settings change on the screen already showing reaches the drafts of how Desk works.
    const style = screen.getByRole('region', { name: 'How Desk works' });
    expect((within(style).getByLabelText(/Detailed/) as HTMLInputElement).checked).toBe(false);
    bridge.emit('desk:event', ev(10, 'project.updated', { settings: { check_in: 'detailed' } }));
    await waitFor(() => expect((within(style).getByLabelText(/Detailed/) as HTMLInputElement).checked).toBe(true));
    expect((within(style).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each([
    ['fails', { code: 'internal', message: 'deskd is not answering', status: 500 }],
    ['is missing', { code: 'not_found', message: 'No project p', status: 404 }],
  ])("says it couldn't load a project that %s", async (_, error) => {
    await setup({
      'projects.get': () => {
        throw error;
      },
    });
    expect(await screen.findByRole('heading', { name: "Couldn't load this project" })).toBeTruthy();
    expect(screen.getByText(error.message)).toBeTruthy();
    expect(screen.queryByText('Loading…')).toBeNull();
  });

  it('keeps each write pending on its own control until that write settles', async () => {
    let addDone!: () => void;
    let saveDone!: () => void;
    const bridge = await setup({
      'app.pickFolder': () => '/Users/me/repo',
      'projects.addSource': () => new Promise<object>((resolve) => (addDone = () => resolve({}))),
      'projects.update': () => new Promise<object>((resolve) => (saveDone = () => resolve({}))),
    });
    const sources = await screen.findByRole('region', { name: 'Sources' });
    const add = within(sources).getByRole('button', { name: 'Add folder…' }) as HTMLButtonElement;
    const policy = screen.getByRole('region', { name: 'Policy' });
    const save = within(policy).getByRole('button', { name: 'Save policy' }) as HTMLButtonElement;
    const pending = (b: HTMLButtonElement) => b.getAttribute('aria-busy') === 'true' && b.disabled;

    fireEvent.click(add);
    await waitFor(() => expect(pending(add)).toBe(true));
    fireEvent.click(within(policy).getByRole('button', { name: 'Move rule 2 up' }));
    fireEvent.click(save);
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'projects.update')).toBe(true));
    await waitFor(() => expect(pending(save)).toBe(true));
    expect(pending(add)).toBe(true);

    // The policy saves first: its button lets go, "Add folder…" stays pending until its own call settles.
    saveDone();
    await waitFor(() => expect(pending(save)).toBe(false));
    expect(pending(add)).toBe(true);
    addDone();
    await waitFor(() => expect(pending(add)).toBe(false));
  });

  it("shows a source's write access as deskd has it until the change lands, and says why deskd refuses a folder", async () => {
    const bridge = await setup({
      'projects.setSourceWrite': () => ({}),
      'app.pickFolder': () => '/Users/me',
      'projects.addSource': () => {
        throw { code: 'invalid_input', message: 'A project source cannot be your home folder: /Users/me', status: 400 };
      },
    });
    const sources = await screen.findByRole('region', { name: 'Sources' });
    const write = within(sources).getByLabelText('Agents can write here') as HTMLInputElement;
    fireEvent.click(write);
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'projects.setSourceWrite')).toBe(true));
    expect(write.checked).toBe(true);
    bridge.emit('desk:event', ev(1, 'source.updated', { source_id: 's1', agent_write: false }));
    await waitFor(() => expect(write.checked).toBe(false));

    fireEvent.click(within(sources).getByRole('button', { name: 'Add folder…' }));
    await waitFor(() => expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['A project source cannot be your home folder: /Users/me']));
    expect(within(sources).getAllByRole('listitem')).toHaveLength(1);
    expect((within(sources).getByRole('button', { name: 'Add folder…' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('adds a folder chosen in the folder browser, and lists it with its own write switch once deskd has it', async () => {
    const bridge = await setup({ 'fs.listDirs': listDirs, 'projects.addSource': () => ({}), 'projects.setSourceWrite': () => ({}) }, [], true);
    const sources = await screen.findByRole('region', { name: 'Sources' });
    const added = () => bridge.calls.filter((c) => c.channel === 'projects.addSource').map((c) => c.input);

    // Cancelling the browser adds nothing.
    fireEvent.click(within(sources).getByRole('button', { name: 'Add folder…' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Choose a folder' })).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(added()).toEqual([]);

    // app.pickFolder asks with purpose source; the browser opens home, then code, and chooses it.
    fireEvent.click(within(sources).getByRole('button', { name: 'Add folder…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Choose a folder' });
    expect(bridge.calls.filter((c) => c.channel === 'app.pickFolder').map((c) => c.input)).toEqual([{ purpose: 'source' }, { purpose: 'source' }]);
    fireEvent.click(await within(dialog).findByRole('button', { name: 'code' }));
    await within(dialog).findByText('No folders here.');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Choose this folder' }));
    await waitFor(() => expect(added()).toEqual([{ id: 'p', source: { path: '/Users/me/code' } }]));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // The list follows deskd's event: the git source, writable by default, with its own switch.
    expect(within(sources).getAllByRole('listitem')).toHaveLength(1);
    bridge.emit('desk:event', ev(1, 'source.added', { source_id: 's2', path: '/Users/me/code', kind: 'git', label: 'code' }));
    await waitFor(() => expect(within(sources).getAllByRole('listitem')).toHaveLength(2));
    const code = within(sources).getAllByRole('listitem')[1]!;
    expect(within(code).getByText('git').className).toBe('chip chip-run');
    expect(within(code).getByText('/Users/me/code').className).toBe('mono small muted');
    expect(code.querySelector('.grow')!.textContent).toBe('code /Users/me/code');
    expect(within(code).getByRole('button', { name: 'Remove code' })).toBeTruthy();
    const write = within(code).getByLabelText('Agents can write here') as HTMLInputElement;
    expect(write.checked).toBe(true);
    fireEvent.click(write);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.setSourceWrite')?.input).toEqual({ id: 'p', sourceId: 's2', agentWrite: false }));
    expect((within(sources).getAllByLabelText('Agents can write here') as HTMLInputElement[]).map((b) => b.checked)).toEqual([true, true]);
  });

  it('keeps every draft when a push rebuilds the project with the same values', async () => {
    const bridge = await setup();
    const goal = (await screen.findByLabelText('Goal')) as HTMLTextAreaElement;
    fireEvent.input(goal, { target: { value: 'File by April' } });
    const style = screen.getByRole('region', { name: 'How Desk works' });
    fireEvent.click(within(style).getByLabelText(/Detailed/));
    const policy = screen.getByRole('region', { name: 'Policy' });
    fireEvent.click(within(policy).getByRole('button', { name: 'Add rule' }));
    // Another client saves the same instructions: the project and its settings come back as new objects with the same values.
    // A source event after it shows when both have landed.
    bridge.emit('desk:event', ev(1, 'project.updated', { instructions: '' }));
    bridge.emit('desk:event', ev(2, 'source.updated', { source_id: 's1', agent_write: false }));
    await waitFor(() => expect((screen.getByLabelText('Agents can write here') as HTMLInputElement).checked).toBe(false));
    expect(goal.value).toBe('File by April');
    expect((within(style).getByLabelText(/Detailed/) as HTMLInputElement).checked).toBe(true);
    expect(within(policy).getAllByRole('listitem')).toHaveLength(DEFAULT_POLICY.length + 1);
    expect((within(style).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('is what #/p/<id>/settings shows', () => {
    expect(screenFor({ name: 'project', id: 'p', tab: 'settings' })).toEqual({ component: SettingsScreen, inputs: { projectId: 'p' } });
  });
});
