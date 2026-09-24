import { describe, expect, it } from 'vitest';
import { layoutMap, type MapProject } from './layout';

const projects = (n: number): MapProject[] =>
  Array.from({ length: n }, (_, i) => ({ id: `p${i}`, activity: (i * 7) % 11 + 1, threads: Array.from({ length: i % 4 }, (_, j) => ({ id: `t${i}-${j}`, status: 'running' as const })) }));

describe('layoutMap', () => {
  it('is deterministic, in bounds, clear of the sun, and never overlaps', () => {
    for (const n of [1, 3, 5, 8]) {
      const a = layoutMap(projects(n), 1000, 700);
      expect(layoutMap(projects(n), 1000, 700)).toEqual(a);
      for (const t of a.territories) {
        expect(t.x - t.r).toBeGreaterThanOrEqual(0);
        expect(t.y - t.r).toBeGreaterThanOrEqual(0);
        expect(t.x + t.r).toBeLessThanOrEqual(1000);
        expect(t.y + t.r).toBeLessThanOrEqual(700);
        expect(Math.hypot(t.x - a.sun.x, t.y - a.sun.y)).toBeGreaterThanOrEqual(t.r + 40);
      }
      for (let i = 0; i < a.territories.length; i++)
        for (let j = i + 1; j < a.territories.length; j++) {
          const p = a.territories[i]!;
          const q = a.territories[j]!;
          expect(Math.hypot(p.x - q.x, p.y - q.y)).toBeGreaterThanOrEqual(p.r + q.r);
        }
    }
  });

  it('makes busier projects larger and puts threads on their orbit', () => {
    const [quiet, busy] = layoutMap(
      [
        { id: 'quiet', activity: 1, threads: [] },
        { id: 'busy', activity: 20, threads: [{ id: 't1', status: 'running' }, { id: 't2', status: 'waiting' }] },
      ],
      1200,
      800,
    ).territories.sort((x, y) => x.id.localeCompare(y.id)).reverse();
    expect(busy!.r).toBeGreaterThan(quiet!.r);
    for (const t of busy!.threads) expect(Math.hypot(t.x - busy!.x, t.y - busy!.y)).toBeCloseTo(busy!.orbit, 5);
  });
});
