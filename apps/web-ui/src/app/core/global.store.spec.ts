import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import { FakeDeskBridge, provideGlobal } from '../testing/fake-bridge';
import { GlobalStore } from './global.store';

const at = (status: GlobalState['connection']['status']): GlobalState => ({ ...initialGlobalState(), connection: { status } });

describe('GlobalStore', () => {
  it('seeds from broker.snapshot, then follows desk:global until stopped', async () => {
    const bridge = new FakeDeskBridge({ 'broker.snapshot': () => at('live') });
    TestBed.configureTestingModule({ providers: bridge.providers });
    const store = TestBed.inject(GlobalStore);
    expect(store.state().connection.status).toBe('starting');
    const stop = store.start();
    await vi.waitFor(() => expect(store.state().connection.status).toBe('live'));
    bridge.emit('desk:global', at('offline'));
    expect(store.state().connection.status).toBe('offline');
    stop();
    bridge.emit('desk:global', at('live'));
    expect(store.state().connection.status).toBe('offline');
  });

  it('keeps a push that lands before the snapshot', async () => {
    let answer: (s: GlobalState) => void = () => {};
    const bridge = new FakeDeskBridge({ 'broker.snapshot': () => new Promise<GlobalState>((resolve) => (answer = resolve)) });
    TestBed.configureTestingModule({ providers: bridge.providers });
    const store = TestBed.inject(GlobalStore);
    store.start();
    bridge.emit('desk:global', at('mismatch'));
    answer(at('live'));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(store.state().connection.status).toBe('mismatch');
  });

  it('starts from a given state in specs (provideGlobal)', () => {
    const bridge = new FakeDeskBridge();
    TestBed.configureTestingModule({ providers: [...bridge.providers, provideGlobal(at('reconnecting'))] });
    expect(TestBed.inject(GlobalStore).state().connection.status).toBe('reconnecting');
  });
});
