import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { DeskCallError } from '../core/desk-bridge';
import { injectLive } from './live';

type V = { n: number; seen: number[] };
const moved = (id: number) => ev(id, 'automation.layout_saved', { automation_id: 'a', layout: {} });
const switched = (id: number) => ev(id, 'automation.switched', { automation_id: 'a', enabled: true, by: 'user' });
const reduce = (v: V, e: StoredEvent): V => ({ ...v, seen: [...v.seen, e.id] });
const refetch = (e: StoredEvent) => e.type === 'automation.switched';

@Component({
  selector: 'desk-live-probe',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<output>{{ text() }}</output>`,
})
class Probe {
  readonly id = input.required<string>();
  readonly events = input.required<StoredEvent[]>();
  readonly ready = input.required<boolean>();
  readonly load = input.required<() => Promise<V>>();
  readonly reduce = input<((v: V, e: StoredEvent) => V) | undefined>(undefined);
  private readonly live = injectLive<V>(() => {
    const r = this.reduce();
    return { key: this.id(), events: this.events(), ready: this.ready(), load: this.load(), ...(r ? { reduce: r } : {}), refetch, debounceMs: 10 };
  });
  protected readonly text = computed(() => `${this.live.status()}:${JSON.stringify(this.live.value())}`);
}

describe('injectLive', () => {
  it('waits for the session, folds later events at once, and reloads once for a burst of matching ones', async () => {
    let n = 0;
    const load = vi.fn(async (): Promise<V> => ({ n: ++n, seen: [] }));
    const view = await render(Probe, { inputs: { id: 'a', events: [], ready: false, load, reduce } });
    expect(load).not.toHaveBeenCalled();
    await view.rerender({ inputs: { id: 'a', events: [moved(1)], ready: true, load, reduce } });
    await screen.findByText('ready:{"n":1,"seen":[]}');
    await view.rerender({ inputs: { id: 'a', events: [moved(1), moved(2)], ready: true, load, reduce } });
    await screen.findByText('ready:{"n":1,"seen":[2]}');
    await view.rerender({ inputs: { id: 'a', events: [moved(1), moved(2), switched(3), switched(4)], ready: true, load, reduce } });
    await screen.findByText('ready:{"n":2,"seen":[]}');
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('reports a 404 as missing', async () => {
    const load = vi.fn(async (): Promise<V> => {
      throw new DeskCallError({ code: 'not_found', message: 'gone', status: 404 });
    });
    await render(Probe, { inputs: { id: 'a', events: [], ready: true, load } });
    await screen.findByText('missing:null');
  });
});
