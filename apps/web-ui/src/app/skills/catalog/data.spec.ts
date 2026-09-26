import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { render } from '@testing-library/angular';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import { GlobalStore } from '../../core/global.store';
import { catalogItems, install } from '../../testing/catalog';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../../testing/fake-bridge';
import { actionFor, BAYS, catalogIndex, injectCatalog, installRef, runtimePackages, runtimeWords, sourceLabel } from './data';

afterEach(() => {
  vi.useRealTimers();
});

/** A component that only holds injectCatalog(), as SkillsScreen does. */
@Component({ selector: 'desk-catalog-probe', template: '' })
class Probe {
  readonly catalog = injectCatalog();
}

async function setup(handlers: FakeHandlers) {
  const bridge = new FakeDeskBridge(handlers);
  const view = await render(Probe, { providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } })] });
  const lists = () => bridge.calls.filter((c) => c.channel === 'catalog.list').length;
  return { fixture: view.fixture, catalog: view.fixture.componentInstance.catalog, lists };
}

describe('runtimeWords', () => {
  it('names what Desk sets up, including a browser', () => {
    const py = { version: '3.12', packages: [] };
    expect(runtimeWords({ runtime: { python: py, extras: ['browser'] } })).toBe('Python 3.12 + browser · set up by Desk');
    expect(runtimeWords({ runtime: { python: py, extras: ['playwright-chromium'] } })).toBe('Python 3.12 + Chromium · set up by Desk');
    expect(runtimeWords({ runtime: {} })).toBe('Nothing to set up');
  });
});

describe('the catalog helpers', () => {
  it('names the source, the packages Desk installs and the action a card offers', () => {
    const [paper, word] = catalogItems();
    expect(sourceLabel(paper!)).toBe('K-Dense-AI/scientific-agent-skills');
    expect(sourceLabel(word!)).toBe('Desk');
    expect(runtimePackages(word!)).toEqual(['python-docx==1.2.0']);
    expect(
      runtimePackages({
        runtime: {
          node: {
            lock: [
              { name: 'marked', version: '14.1.0', integrity: 'sha512-AAAA', path: 'node_modules/marked' },
              { name: 'ms', version: '2.1.3', integrity: 'sha512-BBBB', path: 'node_modules/marked/node_modules/ms' },
            ],
          },
        },
      }),
    ).toEqual(['marked@14.1.0']);
    expect(actionFor(undefined)).toEqual({ label: 'Install', kind: 'install' });
    expect(actionFor(install())).toEqual({ label: 'Installed', kind: 'installed' });
    expect(actionFor(install({ state: 'update_available' }))).toEqual({ label: 'Update', kind: 'update' });
    expect(actionFor(install({ state: 'modified' }))).toEqual({ label: 'Modified', kind: 'modified' });
    expect(actionFor(install({ state: 'name_taken' }))).toEqual({ label: 'Name taken', kind: 'taken' });
    expect(actionFor(install({ state: 'not_installed' }))).toEqual({ label: 'Install', kind: 'install' });
  });

  it('indexes the installs that came from the catalog by skill key, and knows its five bays', () => {
    const items = catalogItems({
      'word-documents': [install(), install({ scope: 'project', project_id: 'p1', state: 'name_taken', sha: null })],
      'pre-mortem': [install({ scope: 'project', project_id: 'p2', state: 'modified' })],
    });
    const index = catalogIndex(items);
    expect([...index.keys()]).toEqual(['global:word-documents', 'project:p2:pre-mortem']);
    expect(index.get('project:p2:pre-mortem')?.install.state).toBe('modified');
    expect(installRef('pre-mortem', { scope: 'project', project_id: 'p2' })).toEqual({ scope: 'project', projectId: 'p2', name: 'pre-mortem' });
    expect(installRef('pre-mortem', { scope: 'global', project_id: null })).toEqual({ scope: 'global', name: 'pre-mortem' });
    expect(BAYS.map((b) => b.category)).toEqual(['research', 'documents', 'writing', 'planning', 'code']);
  });
});

describe('injectCatalog', () => {
  it('loads the catalog, and lists again when a runtime changes state or the window gets focus', async () => {
    // Held until the loading state is checked: render's whenStable would otherwise see a one-call refresh answered.
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const { fixture, catalog, lists } = await setup({
      'catalog.list': async () => {
        await gate;
        return catalogItems();
      },
    });
    expect(catalog.status()).toBe('loading');
    release();
    await vi.waitFor(() => expect(catalog.status()).toBe('ready'));
    expect(catalog.items().map((i) => i.id)).toEqual(['paper-lookup', 'word-documents', 'pre-mortem']);
    expect(lists()).toBe(1);
    TestBed.inject(GlobalStore).set((g) => ({ ...g, runtimes: { progress: {}, seq: 1 } }));
    await vi.waitFor(() => expect(lists()).toBe(2));
    TestBed.inject(GlobalStore).set((g) => ({ ...g, connection: { status: 'reconnecting' } }));
    await fixture.whenStable();
    expect(lists()).toBe(2);
    window.dispatchEvent(new Event('focus'));
    await vi.waitFor(() => expect(lists()).toBe(3));
  });

  it('polls every 3 s while a runtime is being set up, and stops once it is ready', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    let runtime: 'preparing' | 'ready' = 'preparing';
    const { fixture, catalog, lists } = await setup({ 'catalog.list': () => catalogItems({ 'paper-lookup': [install({ runtime })] }) });
    await vi.waitFor(() => expect(catalog.items()[0]?.installs[0]?.runtime).toBe('preparing'));
    await fixture.whenStable();
    const first = lists();
    vi.advanceTimersByTime(3000);
    expect(lists()).toBe(first + 1);
    runtime = 'ready';
    vi.advanceTimersByTime(3000);
    await vi.waitFor(() => expect(catalog.items()[0]?.installs[0]?.runtime).toBe('ready'));
    await fixture.whenStable();
    const settled = lists();
    vi.advanceTimersByTime(9000);
    expect(lists()).toBe(settled);
  });

  it("reports a catalog it can't load", async () => {
    const { catalog } = await setup({
      'catalog.list': () => {
        throw { code: 'internal', message: 'deskd is not answering' };
      },
    });
    await vi.waitFor(() => expect(catalog.status()).toBe('error'));
    expect(catalog.error()).toBe('deskd is not answering');
    expect(catalog.items()).toEqual([]);
  });
});
