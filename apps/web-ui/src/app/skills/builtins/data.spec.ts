import { Component } from '@angular/core';
import { render } from '@testing-library/angular';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { builtin } from '../../testing/builtins';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../../testing/fake-bridge';
import { injectBuiltins } from './data';

afterEach(() => {
  vi.useRealTimers();
});

/** A component that only holds injectBuiltins(), as SkillsScreen does. */
@Component({ selector: 'desk-builtins-probe', template: '' })
class Probe {
  readonly builtins = injectBuiltins();
}

async function setup(handlers: FakeHandlers) {
  const bridge = new FakeDeskBridge(handlers);
  const view = await render(Probe, { providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } })] });
  const lists = () => bridge.calls.filter((c) => c.channel === 'builtins.list').length;
  return { fixture: view.fixture, builtins: view.fixture.componentInstance.builtins, lists };
}

/** Lets every pending bridge answer land (a macrotask runs after all queued microtasks). */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

const b = (state: BuiltinSkillInfo['runtime']['state'], reason: string | null = null) => builtin('pdf-toolkit', { title: 'PDF toolkit', runtime: { state, reason } });

describe('injectBuiltins', () => {
  it('loads the built-ins, lists again on focus, and keeps them when a list fails', async () => {
    // Held until the loading state is checked: render's whenStable would otherwise see a one-call refresh answered.
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    let fail = false;
    const { builtins, lists } = await setup({
      'builtins.list': async () => {
        await gate;
        if (fail) throw { code: 'internal', message: 'deskd is not answering' };
        return [builtin('pdf-toolkit'), builtin('images')];
      },
    });
    expect(builtins.status()).toBe('loading');
    release();
    await vi.waitFor(() => expect(builtins.status()).toBe('ready'));
    expect(builtins.items().map((i) => i.name)).toEqual(['pdf-toolkit', 'images']);
    fail = true;
    window.dispatchEvent(new Event('focus'));
    await vi.waitFor(() => expect(builtins.error()).toBe('deskd is not answering'));
    expect(lists()).toBe(2);
    expect(builtins.status()).toBe('ready');
    expect(builtins.items()).toHaveLength(2);
  });

  it('polls every 2 s while one is being set up, then every 30 s', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    let state: 'preparing' | 'ready' = 'preparing';
    const { fixture, builtins, lists } = await setup({ 'builtins.list': () => [b(state)] });
    await vi.waitFor(() => expect(builtins.items()[0]?.runtime.state).toBe('preparing'));
    await fixture.whenStable();
    await settle();
    const first = lists();
    vi.advanceTimersByTime(2_000);
    expect(lists()).toBe(first + 1);
    state = 'ready';
    vi.advanceTimersByTime(2_000);
    await vi.waitFor(() => expect(builtins.items()[0]?.runtime.state).toBe('ready'));
    await fixture.whenStable();
    await settle();
    const settled = lists();
    vi.advanceTimersByTime(20_000);
    expect(lists()).toBe(settled);
    vi.advanceTimersByTime(10_000);
    expect(lists()).toBe(settled + 1);
  });

  it('never overlaps a slow list: asks meanwhile list once more when it ends, and that newer list lands', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    let calls = 0;
    const { builtins, lists } = await setup({
      'builtins.list': async () => {
        if (++calls > 1) return [builtin('pdf-toolkit', { enabled: false })];
        await gate;
        return [builtin('pdf-toolkit')];
      },
    });
    window.dispatchEvent(new Event('focus'));
    window.dispatchEvent(new Event('focus'));
    await settle();
    expect(lists()).toBe(1);
    release();
    await vi.waitFor(() => expect(builtins.items()[0]?.enabled).toBe(false));
    await settle();
    expect(lists()).toBe(2);
  });

  it('lists nothing more once its component is gone, not even a list asked for while one ran', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const { fixture, lists } = await setup({
      'builtins.list': async () => {
        await gate;
        return [b('ready')];
      },
    });
    window.dispatchEvent(new Event('focus'));
    fixture.destroy();
    release();
    await settle();
    expect(lists()).toBe(1);
  });

  it('clears its poll once its component is gone, and neither focus nor time lists again', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const { fixture, builtins, lists } = await setup({ 'builtins.list': () => [b('ready')] });
    await vi.waitFor(() => expect(builtins.status()).toBe('ready'));
    await fixture.whenStable();
    await settle();
    const before = lists();
    expect(vi.getTimerCount()).toBe(1);
    fixture.destroy();
    expect(vi.getTimerCount()).toBe(0);
    window.dispatchEvent(new Event('focus'));
    vi.advanceTimersByTime(60_000);
    await settle();
    expect(lists()).toBe(before);
  });

  it("is an error until a list arrives", async () => {
    const { builtins } = await setup({ 'builtins.list': () => Promise.reject({ code: 'internal', message: 'deskd is not answering' }) });
    await vi.waitFor(() => expect(builtins.status()).toBe('error'));
    expect(builtins.items()).toEqual([]);
  });
});
