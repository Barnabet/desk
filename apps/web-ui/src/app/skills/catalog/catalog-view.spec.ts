import { fireEvent, render, screen, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { CatalogItem } from '@desk/protocol';
import { catalogItems, install } from '../../testing/catalog';
import { FakeDeskBridge, provideGlobal } from '../../testing/fake-bridge';
import { CatalogView, injectCatalogLayout, LayoutSwitch, type Layout } from './catalog-view';

beforeEach(() => {
  localStorage.clear();
});

async function setup(items: CatalogItem[], layout: Layout = 'cards') {
  const reviewed: string[] = [];
  await render(`<div deskCatalogView [items]="items" [layout]="layout" [projectNames]="names" (review)="reviewed.push($event)"></div>`, {
    imports: [CatalogView],
    componentProperties: { items, layout, names: new Map([['p1', 'Onboarding'], ['p2', 'Tax']]), reviewed },
    providers: [...new FakeDeskBridge().providers, provideGlobal(initialGlobalState())],
  });
  return { reviewed };
}

describe('CatalogView', () => {
  it('shows the bays that have entries, each card with its chips, runtime, where else it is installed and its action', async () => {
    const { reviewed } = await setup(
      catalogItems({
        'paper-lookup': [install({ scope: 'project', project_id: 'p2' })],
        'word-documents': [install()],
        'pre-mortem': [install({ state: 'name_taken', sha: null })],
      }),
    );
    expect(screen.getAllByRole('region').map((r) => r.getAttribute('aria-labelledby'))).toEqual(['bay-research', 'bay-documents', 'bay-planning']);
    const research = screen.getByRole('region', { name: 'Research' });
    expect(research.querySelector('.catalog-bay-head .muted')?.textContent).toBe('Find sources, check facts, read the web. · 1');

    const paper = within(research).getByRole('listitem', { name: 'Paper lookup' });
    expect(paper.className).toBe('catalog-card card');
    expect([...paper.querySelectorAll('.catalog-chips .chip')].map((c) => [c.className, c.textContent])).toEqual([
      ['chip chip-idle', 'K-Dense-AI/scientific-agent-skills'],
      ['chip chip-idle', 'MIT'],
      ['chip chip-wait', '2 scripts'],
    ]);
    expect(paper.querySelector('.catalog-runtime')?.textContent).toBe('Python 3.12 · set up by Desk');
    expect(paper.querySelector('.catalog-card-foot .grow')?.textContent).toBe('In Tax');
    expect(within(paper).queryByRole('status')).toBeNull();
    fireEvent.click(within(paper).getByRole('button', { name: 'Install Paper lookup' }));
    fireEvent.click(within(paper).getByRole('button', { name: /Search free scholarly APIs for papers\./ }));
    expect(reviewed).toEqual(['paper-lookup', 'paper-lookup']);

    const word = screen.getByRole('listitem', { name: 'Word documents' });
    expect(word.querySelector('.catalog-chips .chip')?.className).toBe('chip chip-done');
    const installed = within(word).getByRole('link', { name: '✓ Installed' });
    expect(installed.className).toBe('btn btn-ghost btn-sm catalog-installed');
    expect(installed.getAttribute('href')).toBe('#/skills/global%3Aword-documents');
    expect(within(word).getByRole('status').textContent).toBe('Ready · Python 3.12 · set up by Desk');

    const pre = screen.getByRole('listitem', { name: 'Pre-mortem' });
    expect(within(pre).getByText('Name taken').getAttribute('title')).toBe("You already have a skill named pre-mortem that didn't come from the catalog.");
    expect(pre.querySelector('.catalog-runtime')?.textContent).toBe('Nothing to set up');
    expect(pre.querySelector('.chip-wait')).toBeNull();
    expect(within(pre).queryByRole('status')).toBeNull();
  });

  it('shows compact rows, and offers a review for edited installs and an update', async () => {
    const { reviewed } = await setup(
      catalogItems({ 'word-documents': [install({ state: 'modified' })], 'pre-mortem': [install({ state: 'update_available', runtime: 'none' }), install({ scope: 'project', project_id: 'p1' })] }),
      'list',
    );
    expect(document.querySelector('.catalog-cards')).toBeNull();
    expect([...document.querySelectorAll('.catalog-rows > li')].map((r) => r.querySelector('.catalog-row-meta')?.textContent)).toEqual([
      'K-Dense-AI/scientific-agent-skills · MIT · 2 scripts',
      'Desk · MIT · 3 scripts',
      'phuryn/pm-skills · MIT · In Onboarding',
    ]);
    const edited = screen.getByRole('button', { name: 'Review Word documents (edited since install)' });
    expect(edited.textContent).toBe('Modified · review');
    expect(edited.className).toBe('btn btn-sm btn-secondary');
    expect(screen.getByRole('button', { name: 'Update Pre-mortem' }).className).toBe('btn btn-sm btn-secondary');
    expect(screen.getByRole('button', { name: 'Install Paper lookup' }).className).toBe('btn btn-sm btn-primary');
    fireEvent.click(edited);
    fireEvent.click(screen.getByRole('button', { name: /^Pre-mortem/ }));
    expect(reviewed).toEqual(['word-documents', 'pre-mortem']);
  });
});

describe('LayoutSwitch and injectCatalogLayout', () => {
  it('switches between cards and compact rows, remembered per viewer', async () => {
    const changed: Layout[] = [];
    await render(`<div deskLayoutSwitch [layout]="layout" (changed)="changed.push($event)"></div>`, { imports: [LayoutSwitch], componentProperties: { layout: 'cards', changed } });
    const group = screen.getByRole('group', { name: 'Layout' });
    expect(group.className).toBe('segmented');
    expect(within(group).getAllByRole('button').map((b) => [b.textContent, b.getAttribute('aria-pressed')])).toEqual([
      ['Cards', 'true'],
      ['Compact', 'false'],
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'Compact' }));
    expect(changed).toEqual(['list']);

    const layout = injectCatalogLayout();
    expect(layout.layout()).toBe('cards');
    layout.set('list');
    expect(layout.layout()).toBe('list');
    expect(localStorage.getItem('desk.catalogLayout')).toBe('list');
    expect(injectCatalogLayout().layout()).toBe('list');
  });
});
