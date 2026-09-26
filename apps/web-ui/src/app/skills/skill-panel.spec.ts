import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { SkillNode } from '@desk/client';
import type { CatalogInstall, CatalogItem } from '@desk/protocol';
import type { SkillRef } from '@desk/ui-core';
import { ToastService } from '../components/toast';
import { catalogItems, install } from '../testing/catalog';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { SkillPanel } from './skill-panel';

beforeEach(() => {
  window.location.hash = '#/skills/global%3Aemail-sequence';
});

const at = () => new Date().toISOString();
const FILES = [
  { path: 'SKILL.md', size: 40 },
  { path: 'scripts/count.py', size: 12 },
];
const detail = (version: number, instructions: string, files = FILES) => ({ name: 'email-sequence', scope: 'global', description: 'Writes onboarding emails', dir: '/s/email-sequence', version, instructions, frontmatter: {}, files });
const history = () => [
  { version: 1, description: 'd', current: false, change_note: 'First draft', origin: 'user', ts: at() },
  { version: 2, description: 'd', current: true, change_note: 'Added a voice check', origin: 'agent:t1', ts: at() },
];
const node = (extra: Partial<SkillNode> = {}): SkillNode => ({ key: 'global:email-sequence', name: 'email-sequence', scope: 'global', projectId: null, projectName: null, version: 2, description: 'Writes onboarding emails', shadowedIn: [], shadows: false, usedBy: [], ...extra });

async function setup(o: { skill?: SkillRef; node?: SkillNode; catalog?: { item: CatalogItem; install: CatalogInstall }; handlers?: FakeHandlers } = {}) {
  const bridge = new FakeDeskBridge({ 'skills.get': () => detail(2, 'Plan the sequence.'), 'skills.history': () => history(), ...o.handlers });
  const outputs = { edit: vi.fn(), askDesk: vi.fn(), changed: vi.fn(), close: vi.fn() };
  const view = await render(SkillPanel, {
    inputs: {
      skill: o.skill ?? { scope: 'global', name: 'email-sequence' },
      node: o.node,
      catalog: o.catalog,
      projectNames: new Map([['p1', 'Onboarding'], ['p2', 'Tax']]),
      threadTitles: new Map([['t1', 'Welcome emails']]),
      version: 0,
    },
    on: outputs,
    providers: [...bridge.providers, provideGlobal(initialGlobalState())],
    // The host must be the <article> its selector names (TestBed uses a <div> otherwise), or getByRole('article') finds nothing.
    configureTestBed: (testBed) => testBed.configureTestingModule({ inferTagName: true }),
  });
  return { bridge, view, outputs, panel: screen.getByRole('article') };
}

describe('SkillPanel', () => {
  it('shows the overview: shadowing, the change note, the versions and who uses the skill now', async () => {
    const { panel } = await setup({ node: node({ shadowedIn: ['p1'], usedBy: [{ threadId: 't1', title: 'Welcome emails', status: 'running', projectId: 'p1' }] }) });
    expect(panel.getAttribute('aria-label')).toBe('Skill email-sequence');
    expect(panel.className).toBe('card skill-panel');
    expect(panel.querySelector('.skill-scope')?.textContent).toBe('Global');
    expect(await within(panel).findByText('Writes onboarding emails')).toBeTruthy();
    expect(panel.querySelector('.shadow-note')?.textContent).toBe('Onboarding has its own email-sequence, so agents there use that one. Everywhere else they use this.');
    expect(panel.querySelector('.skill-change strong')?.textContent).toBe('Welcome emails · v2 · just now');
    expect(within(panel).getByText('Added a voice check')).toBeTruthy();
    const versions = within(panel).getByRole('group', { name: 'Versions' });
    expect(within(versions).getAllByRole('button').map((b) => [b.textContent, b.getAttribute('aria-pressed'), b.className])).toEqual([
      ['v2', 'true', 'on'],
      ['v1', 'false', ''],
    ]);
    const link = within(panel).getByRole('link', { name: 'Welcome emails' });
    expect(link.getAttribute('href')).toBe('#/p/p1/threads/t1');
    expect(link.parentElement?.textContent).toBe('Welcome emails · running · Onboarding');
    expect(within(panel).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Overview', 'Instructions', 'Files · 2', 'History · 2']);
    fireEvent.click(within(versions).getByRole('button', { name: 'v1' }));
    expect(within(panel).getByRole('tab', { name: 'History · 2' }).getAttribute('aria-selected')).toBe('true');
  });

  it('starts on Overview again for another skill, and fetches that one', async () => {
    const { bridge, view, panel } = await setup();
    await within(panel).findByText('Writes onboarding emails');
    fireEvent.click(within(panel).getByRole('tab', { name: 'Instructions' }));
    expect(await within(panel).findByText('Plan the sequence.')).toBeTruthy();
    await view.rerender({ inputs: { skill: { scope: 'project', projectId: 'p1', name: 'brand-voice' } }, partialUpdate: true });
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'skills.get').map((c) => c.input)).toEqual([{ name: 'email-sequence' }, { projectId: 'p1', name: 'brand-voice' }]));
    expect(panel.getAttribute('aria-label')).toBe('Skill brand-voice');
    expect(within(panel).getByRole('tab', { name: 'Overview' }).getAttribute('aria-selected')).toBe('true');
  });

  it('shows where a catalog skill came from, the way to its update, and a broken SKILL.md', async () => {
    const [paper] = catalogItems();
    const { panel } = await setup({
      skill: { scope: 'project', projectId: 'p2', name: 'paper-lookup' },
      catalog: { item: paper!, install: install({ scope: 'project', project_id: 'p2', state: 'update_available', runtime: 'none' }) },
      handlers: { 'skills.get': () => ({ ...detail(1, 'Look it up.'), name: 'paper-lookup', scope: 'project', error: 'SKILL.md has no description' }), 'skills.history': () => [] },
    });
    const scope = panel.querySelector('.skill-scope')!;
    expect([scope.className, scope.textContent]).toEqual(['skill-scope project', 'Tax']);
    expect(await within(panel).findByText('From catalog')).toBeTruthy();
    expect(panel.querySelector('.skill-catalog-row .grow')?.textContent).toBe('K-Dense-AI/scientific-agent-skills · MIT · Python 3.12 · set up by Desk');
    expect(within(panel).getByRole('alert').textContent).toBe("SKILL.md has a problem, so agents can't use this skill until it's fixed: SKILL.md has no description");
    expect(within(panel).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Overview', 'Instructions', 'Files · 2', 'History · 0']);
    expect(within(panel).getByText('Not in use right now.')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Update available' }));
    expect(window.location.hash).toBe('#/skills/catalog/paper-lookup');
  });

  it("says when it can't load the skill", async () => {
    const { panel } = await setup({
      handlers: {
        'skills.get': () => {
          throw { code: 'not_found', message: 'No skill named email-sequence', status: 404 };
        },
      },
    });
    expect(await within(panel).findByText("Couldn't load this skill")).toBeTruthy();
    expect(panel.textContent).toContain('No skill named email-sequence');
    expect(within(panel).queryByRole('tab')).toBeNull();
  });

  it('fetches the skill and each pair once, even when a call reads a signal that changes later', async () => {
    // The real bridge can read its signedOut signal inside call() (a sign-in retry); neither fetch may track it.
    const signedIn = signal(true);
    const { bridge, panel } = await setup({
      handlers: {
        'skills.get': () => {
          signedIn();
          return detail(2, 'Plan the sequence.');
        },
        'skills.version': ({ version }: { version: number }) => {
          signedIn();
          return detail(version, 'Plan the sequence.');
        },
      },
    });
    fireEvent.click(await within(panel).findByRole('tab', { name: 'History · 2' }));
    expect(await within(panel).findByText('The instructions are the same.')).toBeTruthy();
    signedIn.set(false);
    TestBed.tick();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(bridge.calls.filter((c) => c.channel === 'skills.get')).toHaveLength(1);
    expect(bridge.calls.filter((c) => c.channel === 'skills.version')).toHaveLength(2);
  });

  it('compares any two versions: the instruction lines and the files that changed', async () => {
    const { bridge, panel } = await setup({
      handlers: {
        'skills.version': ({ version }: { version: number }) =>
          version === 1
            ? detail(1, 'Plan the sequence.')
            : detail(2, 'Plan the sequence.\nCheck the voice.', [
                { path: 'SKILL.md', size: 40 },
                { path: 'scripts/count.py', size: 2048 },
                { path: 'scripts/new.py', size: 5 },
              ]),
      },
    });
    fireEvent.click(await within(panel).findByRole('tab', { name: 'History · 2' }));
    expect([...panel.querySelectorAll('.skill-history > li')].map((li) => li.querySelector('.mono')?.textContent)).toEqual(['v2', 'v1']);
    expect(panel.querySelector('.skill-history .chip-done')?.textContent).toBe('current');
    const changes = await within(panel).findByLabelText('Changes from v1 to v2');
    expect(changes.textContent).toBe('  Writes onboarding emails\n  \n  Plan the sequence.\n+ Check the voice.\n');
    expect(changes.querySelector('.diff-line-add')?.textContent).toBe('+ Check the voice.\n');
    expect([...panel.querySelectorAll('.skill-file-changes li')].map((li) => li.textContent)).toEqual(['changed scripts/count.py 12 B → 2.0 KB', 'added scripts/new.py']);
    fireEvent.change(within(panel).getByLabelText('From version'), { target: { value: '2' } });
    expect(await within(panel).findByText('The instructions are the same.')).toBeTruthy();
    expect(panel.querySelector('.skill-file-changes')).toBeNull();
    expect(bridge.calls.filter((c) => c.channel === 'skills.version').map((c) => (c.input as { version: number }).version)).toEqual([1, 2, 2, 2]);
  });

  it('hands Edit and Desk to the screen, restores after a confirm, and deletes, then closes', async () => {
    const { bridge, outputs, panel } = await setup({
      handlers: { 'skills.version': () => detail(1, 'Plan the sequence.'), 'skills.restore': () => ({ version: 3 }), 'skills.remove': () => ({ ok: true }), 'skills.file': () => new TextEncoder().encode('print(1)') },
    });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Edit' }));
    expect(outputs.edit).toHaveBeenCalledWith(expect.objectContaining({ name: 'email-sequence', instructions: 'Plan the sequence.' }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Refine with Desk' }));
    expect(outputs.askDesk).toHaveBeenCalledTimes(1);

    fireEvent.click(within(panel).getByRole('tab', { name: /^Files/ }));
    expect(within(panel).getByText('Pick a file to view it.')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: /scripts\/count\.py/ }));
    expect(await within(panel).findByText('print(1)')).toBeTruthy();
    expect(bridge.calls.find((c) => c.channel === 'skills.file')?.input).toEqual({ name: 'email-sequence', path: 'scripts/count.py' });

    fireEvent.click(within(panel).getByRole('tab', { name: /^History/ }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Restore' }));
    const restore = await screen.findByRole('dialog', { name: 'Restore v1?' });
    expect(restore.textContent).toContain('It comes back as the newest version. Nothing in the history is lost.');
    fireEvent.click(within(restore).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(outputs.changed).toHaveBeenCalledTimes(1));
    expect(bridge.calls.find((c) => c.channel === 'skills.restore')?.input).toEqual({ name: 'email-sequence', version: 1 });
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'skills.get')).toHaveLength(2));

    fireEvent.click(within(panel).getByRole('button', { name: 'Delete' }));
    const remove = await screen.findByRole('dialog', { name: 'Delete email-sequence?' });
    expect(remove.textContent).toContain("Agents stop using it. Its past versions stay in the skill's history on disk.");
    fireEvent.click(within(remove).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(outputs.close).toHaveBeenCalledTimes(1));
    expect(bridge.calls.find((c) => c.channel === 'skills.remove')?.input).toEqual({ name: 'email-sequence' });
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Restored v1 as a new version.', 'Deleted email-sequence.']);
  });

  it('drops a file that lands after another skill opened', async () => {
    let release: (data: Uint8Array) => void = () => {};
    const { view, panel } = await setup({ handlers: { 'skills.file': () => new Promise<Uint8Array>((resolve) => (release = resolve)) } });
    fireEvent.click(await within(panel).findByRole('tab', { name: /^Files/ }));
    fireEvent.click(within(panel).getByRole('button', { name: /scripts\/count\.py/ }));
    await view.rerender({ inputs: { skill: { scope: 'project', projectId: 'p1', name: 'brand-voice' } }, partialUpdate: true });
    await waitFor(() => expect(panel.getAttribute('aria-label')).toBe('Skill brand-voice'));
    release(new TextEncoder().encode('print(1)'));
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(within(panel).getByRole('tab', { name: /^Files/ }));
    expect(within(panel).getByText('Pick a file to view it.')).toBeTruthy();
    expect(within(panel).queryByText('print(1)')).toBeNull();
  });

  it("drops a delete's close that lands after another skill opened, and never marks that skill's Delete busy", async () => {
    let release: (r: { ok: true }) => void = () => {};
    const { view, outputs, panel } = await setup({ handlers: { 'skills.remove': () => new Promise<{ ok: true }>((resolve) => (release = resolve)) } });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Delete' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Delete email-sequence?' })).getByRole('button', { name: 'Delete' }));
    expect(within(panel).getByRole('button', { name: 'Delete' }).getAttribute('aria-busy')).toBe('true');
    // rerender drops the output listeners it is not given again.
    await view.rerender({ inputs: { skill: { scope: 'project', projectId: 'p1', name: 'brand-voice' } }, on: outputs, partialUpdate: true });
    await waitFor(() => expect(panel.getAttribute('aria-label')).toBe('Skill brand-voice'));
    expect(within(panel).getByRole('button', { name: 'Delete' }).getAttribute('aria-busy')).toBeNull();
    release({ ok: true });
    await waitFor(() => expect(outputs.changed).toHaveBeenCalledTimes(1));
    expect(outputs.close).not.toHaveBeenCalled();
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Deleted email-sequence.']);
  });

  it('closes an open confirm when another skill opens, so it never acts on that one', async () => {
    const { bridge, view, panel } = await setup();
    fireEvent.click(await within(panel).findByRole('button', { name: 'Delete' }));
    expect(await screen.findByRole('dialog', { name: 'Delete email-sequence?' })).toBeTruthy();
    await view.rerender({ inputs: { skill: { scope: 'project', projectId: 'p1', name: 'brand-voice' } }, partialUpdate: true });
    await waitFor(() => expect(panel.getAttribute('aria-label')).toBe('Skill brand-voice'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(bridge.calls.filter((c) => c.channel === 'skills.remove')).toEqual([]);
  });

  it("keeps each skill's pending action marked when the user goes from one to another and back", async () => {
    const releases: Array<(r: { ok: true }) => void> = [];
    const { view, outputs, panel } = await setup({ handlers: { 'skills.remove': () => new Promise<{ ok: true }>((resolve) => releases.push(resolve)) } });
    const deleteBusy = () => within(panel).getByRole('button', { name: 'Delete' }).getAttribute('aria-busy');
    const open = async (skill: SkillRef, label: string) => {
      await view.rerender({ inputs: { skill }, on: outputs, partialUpdate: true });
      await waitFor(() => expect(panel.getAttribute('aria-label')).toBe(label));
    };
    const deleteHere = async (name: string) => {
      fireEvent.click(await within(panel).findByRole('button', { name: 'Delete' }));
      fireEvent.click(within(await screen.findByRole('dialog', { name: `Delete ${name}?` })).getByRole('button', { name: 'Delete' }));
    };
    await deleteHere('email-sequence');
    await open({ scope: 'project', projectId: 'p1', name: 'brand-voice' }, 'Skill brand-voice');
    await deleteHere('brand-voice');
    expect(deleteBusy()).toBe('true');
    await open({ scope: 'global', name: 'email-sequence' }, 'Skill email-sequence');
    expect(deleteBusy()).toBe('true');
    releases.forEach((release) => release({ ok: true }));
    await waitFor(() => expect(outputs.changed).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(deleteBusy()).toBeNull());
  });

  it('says when the skill is a copy of a built-in one, with a link to the built-in', async () => {
    const { panel } = await setup({
      skill: { scope: 'global', name: 'pdf-toolkit' },
      handlers: { 'skills.history': () => [{ version: 1, description: 'd', current: true, change_note: 'Duplicated from the built-in skill', origin: 'builtin:pdf-toolkit@0123456789ab', ts: at() }] },
    });
    expect(await within(panel).findByText(/Customised from the built-in skill/)).toBeTruthy();
    expect(panel.querySelector('.shadow-note')?.textContent).toBe('Customised from the built-in skill pdf-toolkit. Agents use this copy instead; delete it to bring the built-in back.');
    expect(within(panel).getByRole('link', { name: 'pdf-toolkit' }).getAttribute('href')).toBe('#/skills/builtin%3Apdf-toolkit');
    expect(panel.querySelector('.skill-change strong')?.textContent).toBe('Built into Desk · v1 · just now');
  });
});
