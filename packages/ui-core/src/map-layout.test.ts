import type { AgentStatus } from '@desk/protocol';
import { describe, expect, it } from 'vitest';
import { layoutMap, type Box, type MapLayout, type MapProject } from './map-layout';

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

  it('starts the moons of a territory without a callout at its upper right, as before', () => {
    const [t] = layoutMap([{ id: 'p', activity: 9, threads: [{ id: 't1', status: 'running' }, { id: 't2', status: 'running' }] }], 1000, 700).territories;
    const [first, second] = t!.threads;
    expect(Math.atan2(first!.y - t!.y, first!.x - t!.x)).toBeCloseTo(-Math.PI / 3, 5);
    expect(Math.atan2(second!.y - t!.y, second!.x - t!.x)).toBeCloseTo((2 * Math.PI) / 3, 5);
  });
});

/** Whether two boxes share any area (touching edges do not count). */
const intersects = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

const SUN: [string, string] = ['1.0.0 · running', 'proxy up · 2 threads running'];
const thread = (id: string, title: string, status: AgentStatus = 'running') => ({ id, title, status });
const moons = (id: string, n: number) => Array.from({ length: n }, (_, i) => thread(`${id}-t${i}`, ['Copy review', 'Signup checklist', 'Funnel analysis', 'Receipts'][i % 4]!, i % 3 ? 'waiting' : 'running'));

/** The screenshots' map: an approval over Onboarding's Desk, and Docs refresh under the sun. */
const onboarding = (activity: number, threads = 2): MapProject => ({ id: 'p1', activity, label: ['Onboarding revamp', '1 running · 1 waiting'], callout: true, threads: moons('p1', threads) });
const docs = (activity: number, threads = 1): MapProject => ({ id: 'p2', activity, label: ['Docs refresh', '1 running'], threads: moons('p2', threads) });
const tax = (activity: number, threads = 3): MapProject => ({ id: 'p3', activity, label: ['Tax paperwork', '1 running · 2 waiting'], callout: true, threads: moons('p3', threads) });

const SIZES: Array<[number, number]> = [
  [1000, 700],
  [680, 760],
  [760, 560],
  [1400, 900],
];

/** No territory's label crosses the sun's, and no callout covers a moon of its territory. */
function expectClear(layout: MapLayout, input: MapProject[], where: string) {
  for (const t of layout.territories) {
    expect(intersects(t.label.box, layout.sunLabel.box), `${where}: ${t.id}'s label and the sun's`).toBe(false);
    if (!input.find((p) => p.id === t.id)!.callout) continue;
    for (const m of t.threads) expect(intersects(t.callout.box, m.box), `${where}: ${t.id}'s callout and its moon ${m.id}`).toBe(false);
  }
}

describe('layoutMap keeps labels and callouts clear', () => {
  it('with two projects, the lower territory keeps its label off the sun’s and the callout off the moons', () => {
    for (const [w, h] of SIZES)
      for (const [a, b] of [[16, 9], [9, 16], [4, 4], [30, 1], [1, 30]] as const) {
        const input = [onboarding(a), docs(b)];
        expectClear(layoutMap(input, w, h, SUN), input, `${w}x${h} activity ${a}/${b}`);
      }
  });

  it('with three projects, every label stays off the sun’s and both callouts off their moons', () => {
    for (const [w, h] of SIZES)
      for (const [a, b, c] of [[16, 9, 5], [5, 16, 9], [9, 5, 16], [30, 30, 30], [1, 1, 1]] as const) {
        const input = [onboarding(a), docs(b, 2), tax(c)];
        expectClear(layoutMap(input, w, h, SUN), input, `${w}x${h} activity ${a}/${b}/${c}`);
      }
  });

  it('keeps a callout off its moons however many threads the territory has', () => {
    for (const [w, h] of SIZES)
      for (let n = 1; n <= 8; n++)
        for (const a of [1, 9, 30]) {
          const input = [onboarding(a, n), docs(9)];
          expectClear(layoutMap(input, w, h, SUN), input, `${w}x${h} activity ${a}, ${n} threads`);
        }
  });

  it('anchors the labels and callouts inside their boxes, as the renderers place them', () => {
    const input = [onboarding(16), docs(9)];
    const layout = layoutMap(input, 1000, 700, SUN);
    const { sunLabel } = layout;
    expect(sunLabel).toMatchObject({ x: layout.sun.x, y: layout.sun.y + 40 });
    expect(sunLabel.box.x + sunLabel.box.w / 2).toBeCloseTo(sunLabel.x, 5);
    expect(sunLabel.box.y).toBe(sunLabel.y);
    for (const t of layout.territories) {
      expect(t.label).toMatchObject({ x: t.x, y: t.y - t.r + 14 });
      expect(t.label.box.x + t.label.box.w / 2).toBeCloseTo(t.label.x, 5);
      expect(t.label.box.y + t.label.box.h).toBeCloseTo(t.label.y, 5);
      expect(t.callout).toMatchObject({ x: t.x, y: t.y - t.desk / 2 - 18 });
      expect(t.callout.box.y + t.callout.box.h).toBeCloseTo(t.callout.y, 5);
    }
  });
});
