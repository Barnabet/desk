import { describe, expect, it } from 'vitest';
import { digestDef } from './testing/automations';
import { ancestors, autoLayout, danglingRoutes, edgeIndex, edgeLabel, ensureLayout, kindLabel, START_ID, startSteps, stepDetail, toGraph } from './automation-graph';

describe('automation graph', () => {
  it('finds start steps and ancestors', () => {
    const def = digestDef();
    expect(startSteps(def)).toEqual(['fetch']);
    expect([...ancestors(def, 'ok')].sort()).toEqual(['fetch', 'sum']);
    expect(ancestors(def, 'fetch').size).toBe(0);
    expect(edgeIndex('edge-3')).toBe(3);
    expect(edgeIndex('__start->fetch')).toBeNull();
  });

  it('labels nodes and edges like the mockup', () => {
    const def = digestDef();
    const [fetch, sum, ok] = def.steps;
    expect(kindLabel(sum!, 1)).toBe('agent');
    expect(kindLabel(sum!, 2)).toBe('agent · join all');
    expect(stepDetail(fetch!)).toBe('digest: fetch.py');
    expect(stepDetail(sum!)).toBe('skills: web-research · publish');
    expect(stepDetail(ok!)).toBe('shows digest.md');
    expect(edgeLabel({ from: 'a', to: 'b', route: 'changed' })).toBe('changed');
    expect(edgeLabel({ from: 'a', to: 'b', when: 'steps.a.outputs.count > 0' })).toBe('if steps.a.outputs.count > 0');
    expect(edgeLabel({ from: 'a', to: 'b', route: 'changed', when: 'inputs.deep == true and steps.a.outputs.n > 3' })).toBe('changed · inputs.deep == true and ste…');
    expect(edgeLabel({ from: 'a', to: 'b' })).toBeNull();
    expect(danglingRoutes(def, fetch!)).toEqual(['unchanged']);
  });

  it('lays out top to bottom, and keeps saved positions', () => {
    const def = digestDef();
    const auto = autoLayout(def);
    expect(auto[START_ID]!.y).toBeLessThan(auto.fetch!.y);
    expect(auto.fetch!.y).toBeLessThan(auto.sum!.y);
    expect(auto.sum!.y).toBeLessThan(auto.ok!.y);
    const saved = { ...auto, sum: { x: 999, y: 400 } };
    expect(ensureLayout(def, saved)).toBe(saved);
    const partial = ensureLayout(def, { [START_ID]: { x: 0, y: 0 }, fetch: { x: 5, y: 90 } });
    expect(partial.fetch).toEqual({ x: 5, y: 90 });
    expect(partial.ok).toEqual(auto.ok);
  });

  it('builds nodes and edges with problems, route stubs, the selection and run looks', () => {
    const def = digestDef();
    const g = toGraph(def, {}, { startLabel: 'Mondays 08:00', selection: { kind: 'step', id: 'sum' }, issues: { steps: { ok: ['question: required'] }, edges: { 1: ['bad'] }, start: [] }, editable: true });
    expect(g.nodes.map((n) => n.id)).toEqual([START_ID, 'fetch', '__stub:fetch:unchanged', 'sum', 'ok']);
    expect(g.nodes.find((n) => n.id === 'sum')?.selected).toBe(true);
    expect(g.nodes.find((n) => n.id === 'ok')?.data).toMatchObject({ errors: ['question: required'], kind: 'ask me' });
    expect(g.edges.map((e) => [e.id, e.source, e.target, e.data.label])).toEqual([
      ['fetch->__stub:fetch:unchanged', 'fetch', '__stub:fetch:unchanged', null],
      [`${START_ID}->fetch`, START_ID, 'fetch', null],
      ['edge-0', 'fetch', 'sum', 'changed'],
      ['edge-1', 'sum', 'ok', null],
    ]);
    expect(g.edges.find((e) => e.id === 'edge-1')?.data.error).toBe(true);
    const lit = toGraph(def, {}, { startLabel: 's', selection: { kind: 'none' }, run: { steps: { fetch: { tone: 'ok', badge: '✓ 12s', detail: null } }, fired: new Set([0]) }, editable: false });
    expect(lit.edges.find((e) => e.id === 'edge-0')?.data.look).toBe('fired');
    expect(lit.edges.find((e) => e.id === 'edge-1')?.data.look).toBe('idle');
    expect(lit.nodes.find((n) => n.id === 'fetch')).toMatchObject({ deletable: false, data: { run: { badge: '✓ 12s' } } });
  });
});
