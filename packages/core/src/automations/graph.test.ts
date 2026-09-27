import { describe, expect, it } from 'vitest';
import { AutomationDefinition, type AutomationEdge, type StepStatus } from '@desk/protocol';
import { TERMINAL_STEP, ancestors, edgeFires, incoming, isSettled, readiness, startSteps, topoOrder, type GraphDef, type StepState } from './graph';

const g = (steps: Array<string | { id: string; join: 'all' | 'any' }>, edges: AutomationEdge[]): GraphDef => ({
  steps: steps.map((s) => (typeof s === 'string' ? { id: s, join: 'all' as const } : s)),
  edges,
});
const st = (status: StepStatus, route: string | null = null): StepState => ({ status, route });
const always = () => true;
const e = (from: string, to: string, extra: Partial<AutomationEdge> = {}): AutomationEdge => ({ from, to, ...extra });

describe('graph structure', () => {
  const diamond = g(['a', 'b', 'c', 'd', 'lone'], [e('a', 'b'), e('a', 'c'), e('b', 'd'), e('c', 'd')]);

  it('finds start steps, incoming edges and ancestors', () => {
    expect(startSteps(diamond)).toEqual(['a', 'lone']);
    expect(incoming(diamond, 'd')).toEqual([e('b', 'd'), e('c', 'd')]);
    expect(incoming(diamond, 'a')).toEqual([]);
    expect(ancestors(diamond, 'd')).toEqual(new Set(['b', 'c', 'a']));
    expect(ancestors(diamond, 'b')).toEqual(new Set(['a']));
    expect(ancestors(diamond, 'a').size).toBe(0);
    expect(ancestors(g(['x', 'y'], [e('x', 'y'), e('y', 'x')]), 'x')).toEqual(new Set(['y'])); // never itself
  });

  it('orders topologically, breaking ties by definition order', () => {
    const fanout = g(['setup', 'fetch_b', 'fetch_a', 'merge', 'notify'], [e('setup', 'fetch_a'), e('setup', 'fetch_b'), e('fetch_a', 'merge'), e('fetch_b', 'merge'), e('merge', 'notify')]);
    expect(topoOrder(fanout)).toEqual(['setup', 'fetch_b', 'fetch_a', 'merge', 'notify']);
    expect(topoOrder(g(['report', 'fetch'], [e('fetch', 'report')]))).toEqual(['fetch', 'report']);
    expect(topoOrder(g(['c', 'a', 'b'], []))).toEqual(['c', 'a', 'b']);
    expect(topoOrder(g(['b', 'x', 'a'], [e('a', 'b'), e('x', 'b')]))).toEqual(['x', 'a', 'b']);
    const parsed = AutomationDefinition.parse({
      title: 'T',
      steps: [
        { id: 'b', title: 'B', kind: 'wait', minutes: 1 },
        { id: 'a', title: 'A', kind: 'wait', minutes: 1 },
      ],
      edges: [{ from: 'a', to: 'b' }],
    });
    expect(topoOrder(parsed)).toEqual(['a', 'b']); // a whole definition fits GraphDef
  });

  it('names a cycle', () => {
    expect(() => topoOrder(g(['a', 'b', 'c'], [e('a', 'b'), e('b', 'c'), e('c', 'a')]))).toThrow('cycle: a → b → c → a');
    expect(() => topoOrder(g(['a', 'b'], [e('b', 'a'), e('a', 'b')]))).toThrow('cycle: a → b → a');
    expect(() => topoOrder(g(['a'], [e('a', 'a')]))).toThrow('cycle: a → a');
    expect(() => topoOrder(g(['start', 'x', 'y', 'tail'], [e('start', 'x'), e('x', 'y'), e('y', 'x'), e('y', 'tail')]))).toThrow('cycle: x → y → x');
  });
});

describe('edge firing', () => {
  it('fires an edge without a route when its source succeeded, with any route', () => {
    const plain = e('a', 'b');
    expect(edgeFires(plain, st('succeeded'), always)).toBe(true);
    expect(edgeFires(plain, st('succeeded', 'changed'), always)).toBe(true);
    for (const s of [st('failed', 'error'), st('failed'), st('rejected', 'rejected'), st('skipped'), st('cancelled'), st('running'), st('waiting'), st('pending')]) {
      expect(edgeFires(plain, s, always), s.status).toBe(false);
    }
  });

  it('fires a routed edge on its route, error and rejected included', () => {
    const changed = e('a', 'b', { route: 'changed' });
    expect(edgeFires(changed, st('succeeded', 'changed'), always)).toBe(true);
    expect(edgeFires(changed, st('succeeded', 'unchanged'), always)).toBe(false);
    expect(edgeFires(changed, st('succeeded'), always)).toBe(false);
    expect(edgeFires(changed, st('running', 'changed'), always)).toBe(false); // a provisional route is not a result
    expect(edgeFires(changed, st('cancelled', 'changed'), always)).toBe(false);
    const error = e('a', 'b', { route: 'error' });
    expect(edgeFires(error, st('failed', 'error'), always)).toBe(true);
    expect(edgeFires(error, st('failed'), always)).toBe(false); // on_error stop leaves no route
    const rejected = e('a', 'b', { route: 'rejected' });
    expect(edgeFires(rejected, st('rejected', 'rejected'), always)).toBe(true);
    expect(edgeFires(rejected, st('skipped'), always)).toBe(false);
  });

  it('asks when last, and only for an edge that could fire', () => {
    const asked: AutomationEdge[] = [];
    const never = (edge: AutomationEdge) => {
      asked.push(edge);
      return false;
    };
    const cond = e('a', 'b', { when: 'steps.a.outputs.count > 0' });
    expect(edgeFires(cond, st('succeeded'), never)).toBe(false);
    expect(asked).toEqual([cond]);
    expect(edgeFires(cond, st('failed', 'error'), never)).toBe(false);
    expect(edgeFires({ ...cond, route: 'go' }, st('succeeded', 'stop'), never)).toBe(false);
    expect(asked).toHaveLength(1);
  });
});

describe('readiness', () => {
  it('runs a linear chain one step at a time', () => {
    const chain = g(['a', 'b', 'c'], [e('a', 'b'), e('b', 'c')]);
    expect(readiness(chain, {}, always)).toEqual({ start: ['a'], skip: [] }); // missing states count as pending
    expect(readiness(chain, { a: st('running') }, always)).toEqual({ start: [], skip: [] });
    expect(readiness(chain, { a: st('waiting') }, always)).toEqual({ start: [], skip: [] });
    expect(readiness(chain, { a: st('succeeded'), b: st('pending'), c: st('pending') }, always)).toEqual({ start: ['b'], skip: [] });
    expect(readiness(chain, { a: st('succeeded'), b: st('succeeded') }, always)).toEqual({ start: ['c'], skip: [] });
    expect(readiness(chain, { a: st('succeeded'), b: st('succeeded'), c: st('succeeded') }, always)).toEqual({ start: [], skip: [] });
  });

  it('fans out in parallel, and a join-all merge after if/else starts once one branch is dead', () => {
    const def = g(
      ['fetch', 'parse_a', 'parse_b', 'check', 'publish', 'hold', 'notify'],
      [
        e('fetch', 'parse_a'),
        e('fetch', 'parse_b'),
        e('parse_a', 'check'),
        e('parse_b', 'check'),
        e('check', 'publish', { route: 'yes' }),
        e('check', 'hold', { route: 'no' }),
        e('publish', 'notify'),
        e('hold', 'notify'),
      ],
    );
    const s: Record<string, StepState> = Object.fromEntries(def.steps.map((x) => [x.id, st('pending')]));
    expect(readiness(def, s, always)).toEqual({ start: ['fetch'], skip: [] });
    s.fetch = st('succeeded');
    expect(readiness(def, s, always)).toEqual({ start: ['parse_a', 'parse_b'], skip: [] });
    s.parse_a = st('succeeded');
    s.parse_b = st('running');
    expect(readiness(def, s, always)).toEqual({ start: [], skip: [] }); // check waits for both
    s.parse_b = st('succeeded');
    expect(readiness(def, s, always)).toEqual({ start: ['check'], skip: [] });
    s.check = st('succeeded', 'yes');
    expect(readiness(def, s, always)).toEqual({ start: ['publish'], skip: ['hold'] });
    s.publish = st('running');
    s.hold = st('skipped');
    expect(readiness(def, s, always)).toEqual({ start: [], skip: [] }); // notify waits for publish
    s.publish = st('succeeded');
    expect(readiness(def, s, always)).toEqual({ start: ['notify'], skip: [] });
    s.notify = st('succeeded');
    expect(isSettled(s)).toBe(true);
  });

  it('starts a join-any step on the first edge that fires, and skips it only when every edge is dead', () => {
    const def = g(['a', 'b', { id: 'c', join: 'any' }], [e('a', 'c'), e('b', 'c')]);
    expect(readiness(def, { a: st('running'), b: st('running'), c: st('pending') }, always)).toEqual({ start: [], skip: [] });
    expect(readiness(def, { a: st('succeeded'), b: st('running'), c: st('pending') }, always)).toEqual({ start: ['c'], skip: [] });
    expect(readiness(def, { a: st('succeeded'), b: st('succeeded'), c: st('running') }, always)).toEqual({ start: [], skip: [] }); // later edges are ignored
    expect(readiness(def, { a: st('failed', 'error'), b: st('running'), c: st('pending') }, always)).toEqual({ start: [], skip: [] });
    expect(readiness(def, { a: st('failed', 'error'), b: st('skipped'), c: st('pending') }, always)).toEqual({ start: [], skip: ['c'] });
    const all = g(['a', 'b', 'c'], [e('a', 'c'), e('b', 'c')]);
    expect(readiness(all, { a: st('succeeded'), b: st('running'), c: st('pending') }, always)).toEqual({ start: [], skip: [] });
    expect(readiness(all, { a: st('cancelled'), b: st('skipped'), c: st('pending') }, always)).toEqual({ start: [], skip: ['c'] });
  });

  it('follows named routes, the error route and the rejected route', () => {
    const def = g(
      ['fetch', 'summarise', 'note_error', 'ask', 'publish', 'tell'],
      [
        e('fetch', 'summarise', { route: 'changed' }),
        e('fetch', 'note_error', { route: 'error' }),
        e('fetch', 'ask'),
        e('ask', 'publish'),
        e('ask', 'tell', { route: 'rejected' }),
      ],
    );
    expect(readiness(def, { fetch: st('succeeded', 'changed') }, always)).toEqual({ start: ['summarise', 'ask'], skip: ['note_error'] });
    expect(readiness(def, { fetch: st('succeeded', 'unchanged') }, always)).toEqual({ start: ['ask'], skip: ['summarise', 'note_error'] });
    expect(readiness(def, { fetch: st('failed', 'error') }, always)).toEqual({ start: ['note_error'], skip: ['summarise', 'ask'] });
    const asked = { fetch: st('succeeded', 'changed'), summarise: st('running'), note_error: st('skipped') };
    expect(readiness(def, { ...asked, ask: st('rejected', 'rejected') }, always)).toEqual({ start: ['tell'], skip: ['publish'] });
    expect(readiness(def, { ...asked, ask: st('succeeded') }, always)).toEqual({ start: ['publish'], skip: ['tell'] });
    expect(readiness(def, { ...asked, ask: st('waiting') }, always)).toEqual({ start: [], skip: [] });
  });

  it('treats an edge whose when is false as dead', () => {
    const def = g(['a', 'b', 'c'], [e('a', 'b', { when: 'steps.a.outputs.count > 0' }), e('a', 'c')]);
    const when = (edge: AutomationEdge) => edge.when !== 'steps.a.outputs.count > 0';
    expect(readiness(def, { a: st('succeeded') }, when)).toEqual({ start: ['c'], skip: ['b'] });
    expect(readiness(def, { a: st('succeeded') }, always)).toEqual({ start: ['b', 'c'], skip: [] });
  });

  it('spreads skips across layers until nothing is left to decide', () => {
    const def = g(['a', 'b', 'c', 'd', 'e', 'f'], [e('a', 'b', { route: 'go' }), e('b', 'c'), e('c', 'd'), e('a', 'e', { route: 'stop' }), e('e', 'f')]);
    const s: Record<string, StepState> = Object.fromEntries(def.steps.map((x) => [x.id, st('pending')]));
    s.a = st('succeeded', 'stop');
    const apply = (r: { start: string[]; skip: string[] }) => {
      for (const id of r.start) s[id] = st('running');
      for (const id of r.skip) s[id] = st('skipped');
      return r;
    };
    expect(apply(readiness(def, s, always))).toEqual({ start: ['e'], skip: ['b'] });
    expect(apply(readiness(def, s, always))).toEqual({ start: [], skip: ['c'] });
    expect(apply(readiness(def, s, always))).toEqual({ start: [], skip: ['d'] });
    expect(readiness(def, s, always)).toEqual({ start: [], skip: [] });
    expect(isSettled(s)).toBe(false); // e is still running
    s.e = st('succeeded');
    expect(apply(readiness(def, s, always))).toEqual({ start: ['f'], skip: [] });
    s.f = st('succeeded');
    expect(isSettled(s)).toBe(true);
  });

  it('reads own state entries only', () => {
    const def = g(['constructor', 'after'], [e('constructor', 'after')]);
    expect(readiness(def, {}, always)).toEqual({ start: ['constructor'], skip: [] });
    expect(readiness(def, { constructor: st('succeeded') }, always)).toEqual({ start: ['after'], skip: [] });
  });
});

describe('settling', () => {
  it('is settled when no step is pending, running or waiting', () => {
    expect([...TERMINAL_STEP].sort()).toEqual(['cancelled', 'failed', 'rejected', 'skipped', 'succeeded']);
    expect(isSettled({})).toBe(true);
    expect(isSettled({ a: st('succeeded'), b: st('skipped'), c: st('failed'), d: st('rejected', 'rejected'), e: st('cancelled') })).toBe(true);
    for (const active of ['pending', 'running', 'waiting'] as const) expect(isSettled({ a: st('succeeded'), b: st(active) }), active).toBe(false);
    expect(isSettled({ a: st('succeeded') }, g(['a', 'b'], []))).toBe(false); // b has not started
    expect(isSettled({ a: st('succeeded'), b: st('skipped') }, g(['a', 'b'], []))).toBe(true);
  });
});
