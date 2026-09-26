import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import type { CatalogInstall } from '@desk/protocol';
import { ToastService } from '../../components/toast';
import { GlobalStore } from '../../core/global.store';
import { catalogItems, install } from '../../testing/catalog';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../../testing/fake-bridge';
import { RuntimeLine } from './runtime-line';

async function setup(at: CatalogInstall, progress: GlobalState['runtimes']['progress'] = {}, handlers: FakeHandlers = {}) {
  const bridge = new FakeDeskBridge(handlers);
  const retried = vi.fn();
  const view = await render(`<div deskRuntimeLine [entry]="entry" [install]="at" (retried)="retried()"></div>`, {
    imports: [RuntimeLine],
    componentProperties: { entry: catalogItems()[0]!, at, retried },
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), runtimes: { progress, seq: 0 } })],
  });
  return { bridge, retried, view };
}

describe('RuntimeLine', () => {
  it('shows nothing for a skill without a runtime', async () => {
    const { view } = await setup(install({ runtime: 'none' }));
    expect(view.container.querySelector('.runtime-line')).toBeNull();
    expect(view.container.textContent).toBe('');
  });

  it('shows Ready with what Desk set up', async () => {
    await setup(install());
    const ready = screen.getByRole('status');
    expect(ready.className).toBe('runtime-line ready');
    expect(ready.textContent).toBe('Ready · Python 3.12 · set up by Desk');
  });

  it('follows the setup step by step while it runs', async () => {
    await setup(install({ scope: 'project', project_id: 'p2', runtime: 'preparing' }), { 'project:p2:paper-lookup': { step: 'Installing packages', done: 1, total: 3 } });
    expect(screen.getByRole('status').className).toBe('runtime-line preparing');
    expect(screen.getByRole('status').textContent).toBe('Setting up… Installing packages (2 of 3)');
    TestBed.inject(GlobalStore).set((g) => ({ ...g, runtimes: { progress: { 'project:p2:paper-lookup': { step: 'Checking the environment' } }, seq: 1 } }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Setting up… Checking the environment'));
    TestBed.inject(GlobalStore).set((g) => ({ ...g, runtimes: { progress: {}, seq: 2 } }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Setting up…'));
  });

  it("explains a failed setup and retries it in the skill's own scope", async () => {
    const { bridge, retried } = await setup(install({ scope: 'project', project_id: 'p2', runtime: 'failed', runtime_reason: 'uv pip failed' }), {}, { 'skills.runtimeRetry': () => ({ state: 'preparing', reason: null }) });
    const alert = screen.getByRole('alert');
    expect(alert.className).toBe('runtime-line failed');
    expect(alert.textContent).toContain("Setup failed: uv pip failed Agents can't run this skill's scripts until it's set up.");
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(retried).toHaveBeenCalledTimes(1));
    expect(bridge.calls).toEqual([{ channel: 'skills.runtimeRetry', input: { projectId: 'p2', name: 'paper-lookup' } }]);
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Setting up paper-lookup again.']);
  });

  it('ends a failure without a reason with a full stop, and says why a retry was refused', async () => {
    await setup(install({ runtime: 'failed' }), {}, {
      'skills.runtimeRetry': () => {
        throw { code: 'conflict', message: 'Skill runtimes are not available in this daemon', status: 409 };
      },
    });
    expect(screen.getByRole('alert').textContent).toContain("Setup failed. Agents can't run");
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Skill runtimes are not available in this daemon']));
  });
});
