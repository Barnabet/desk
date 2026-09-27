// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { DeskCallError } from '../bridge';
import { useLive } from './live';

afterEach(cleanup);

type V = { n: number; seen: number[] };
const moved = (id: number) => ev(id, 'automation.layout_saved', { automation_id: 'a', layout: {} });
const switched = (id: number) => ev(id, 'automation.switched', { automation_id: 'a', enabled: true, by: 'user' });
const reduce = (v: V, e: StoredEvent): V => ({ ...v, seen: [...v.seen, e.id] });
const refetch = (e: StoredEvent) => e.type === 'automation.switched';

function Probe(p: { id: string; events: StoredEvent[]; ready: boolean; load(): Promise<V>; reduce?: (v: V, e: StoredEvent) => V }) {
  const live = useLive({ key: p.id, events: p.events, ready: p.ready, load: p.load, ...(p.reduce ? { reduce: p.reduce } : {}), refetch, debounceMs: 10 });
  return <output>{`${live.status}:${JSON.stringify(live.value)}`}</output>;
}

describe('useLive', () => {
  it('waits for the session, folds later events at once, and reloads once for a burst of matching ones', async () => {
    let n = 0;
    const load = vi.fn(async (): Promise<V> => ({ n: ++n, seen: [] }));
    const { rerender } = render(<Probe id="a" events={[]} ready={false} load={load} reduce={reduce} />);
    expect(load).not.toHaveBeenCalled();
    rerender(<Probe id="a" events={[moved(1)]} ready load={load} reduce={reduce} />);
    await screen.findByText('ready:{"n":1,"seen":[]}');
    rerender(<Probe id="a" events={[moved(1), moved(2)]} ready load={load} reduce={reduce} />);
    await screen.findByText('ready:{"n":1,"seen":[2]}');
    rerender(<Probe id="a" events={[moved(1), moved(2), switched(3), switched(4)]} ready load={load} reduce={reduce} />);
    await screen.findByText('ready:{"n":2,"seen":[]}');
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('reports a 404 as missing', async () => {
    const load = vi.fn(async (): Promise<V> => {
      throw new DeskCallError({ code: 'not_found', message: 'gone', status: 404 });
    });
    render(<Probe id="a" events={[]} ready load={load} />);
    await screen.findByText('missing:null');
  });
});
