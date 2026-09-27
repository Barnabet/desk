import { describe, expect, it } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { digestDef } from './testing/automations';
import { addStep, blankDefinition, connect, patchEdge, patchStep, removeSelection, renameInput, renameStep, setMeta, type AutomationDoc } from './automation-draft';
import { START_ID } from './automation-graph';
import { mapIssues } from './automation-issues';

const doc = (def: AutomationDefinition = digestDef()): AutomationDoc => ({ def, layout: { [START_ID]: { x: 0, y: 0 }, fetch: { x: 20, y: 100 }, sum: { x: 20, y: 210 }, ok: { x: 20, y: 320 } } });

describe('automation draft', () => {
  it('starts a blank definition from a name', () => {
    expect(blankDefinition('weekly-note')).toMatchObject({ title: 'Weekly note', steps: [], edges: [], after_run: 'notify', limits: { run_deadline_hours: 24 } });
  });

  it('adds steps below the selected one, connected, with unique ids', () => {
    const one = addStep(doc(), 'agent', 'sum');
    expect(one.id).toBe('agent');
    expect(one.doc.def.edges.at(-1)).toEqual({ from: 'sum', to: 'agent' });
    expect(one.doc.layout.agent).toEqual({ x: 260, y: 320 }); // sum already has a child (ok), so one column to the right
    expect(one.doc.def.steps.at(-1)).toMatchObject({ id: 'agent', kind: 'agent', brief: '', join: 'all', on_error: 'stop', routes: [], publish: [] });
    const two = addStep(one.doc, 'agent', null);
    expect(two.id).toBe('agent_2');
    expect(two.doc.def.edges).toHaveLength(one.doc.def.edges.length);
    expect(two.doc.layout.agent_2!.y).toBe(430);
  });

  it('removes steps with their edges, positions and output step, and edges by index', () => {
    const d = setMeta(doc(), { output_step: 'sum' });
    const out = removeSelection(d, ['sum'], [0]);
    expect(out.def.steps.map((s) => s.id)).toEqual(['fetch', 'ok']);
    expect(out.def.edges).toEqual([]);
    expect(out.layout.sum).toBeUndefined();
    expect(out.def.output_step).toBeUndefined();
    expect(removeSelection(doc(), [], [1]).def.edges).toEqual([{ from: 'fetch', to: 'sum', route: 'changed' }]);
  });

  it('connects steps, refusing loops, duplicates and self-edges', () => {
    expect(connect(doc(), 'fetch', 'ok')).toMatchObject({ doc: { def: { edges: expect.arrayContaining([{ from: 'fetch', to: 'ok' }]) } } });
    expect(connect(doc(), 'ok', 'fetch')).toEqual({ error: 'That would make a loop: an automation runs top to bottom.' });
    expect(connect(doc(), 'fetch', 'sum')).toEqual({ error: 'These steps are already connected.' });
    expect(connect(doc(), 'sum', 'sum')).toEqual({ error: 'A step cannot come after itself.' });
  });

  it('patches a step, removing fields set to undefined', () => {
    const d = addStep(doc(), 'wait', null).doc;
    const w = patchStep(d, 'wait', { minutes: undefined, until: '08:00' }).def.steps.find((s) => s.id === 'wait');
    expect(w).toMatchObject({ until: '08:00' });
    expect(w && 'minutes' in w).toBe(false);
  });

  it('renames a step everywhere: edges, positions, output step and templates', () => {
    const d = setMeta(doc(), { output_step: 'fetch' });
    const withWhen = patchEdge(d, 1, { when: 'steps.fetch.outputs.count > 0' });
    const r = renameStep(withWhen, 'fetch', 'grab');
    if ('error' in r) throw new Error(r.error);
    expect(r.doc.def.steps[0]!.id).toBe('grab');
    expect(r.doc.def.edges[0]).toEqual({ from: 'grab', to: 'sum', route: 'changed' });
    expect(r.doc.def.edges[1]!.when).toBe('steps.grab.outputs.count > 0');
    expect(r.doc.def.output_step).toBe('grab');
    expect(r.doc.layout.grab).toEqual({ x: 20, y: 100 });
    expect(r.doc.def.steps.find((s) => s.id === 'sum')).toMatchObject({ brief: 'Summarise {{steps.grab.dir}}.' });
    expect(renameStep(d, 'fetch', 'sum')).toEqual({ error: 'Another step is already called sum.' });
    expect('error' in renameStep(d, 'fetch', 'Bad Id')).toBe(true);
  });

  it('renames an input in its templates and schedules', () => {
    const d = doc({ ...digestDef(), triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'UTC', catch_up: 'once', inputs: { topic: 'ai' } }] });
    const r = renameInput(d, 'topic', 'subject');
    if ('error' in r) throw new Error(r.error);
    expect(r.doc.def.inputs[0]!.key).toBe('subject');
    expect(r.doc.def.steps[0]).toMatchObject({ args: ['{{inputs.subject}}'] });
    expect(r.doc.def.triggers[0]!.inputs).toEqual({ subject: 'ai' });
  });

  it('clears an edge condition set to undefined', () => {
    const d = patchEdge(doc(), 0, { when: 'inputs.topic != ""' });
    expect(patchEdge(d, 0, { when: undefined }).def.edges[0]).toEqual({ from: 'fetch', to: 'sum', route: 'changed' });
  });
});

describe('mapIssues', () => {
  it('puts each problem on its step, edge, the Start pill or the automation', () => {
    const m = mapIssues(digestDef(), [
      { path: 'steps[2].question', message: 'Too small' },
      { path: 'steps[1].brief', message: "steps.ok.summary: 'ok' is not upstream of 'sum'" },
      { path: 'edges[0].route', message: 'fetch has no route x' },
      { path: 'triggers[0].cron', message: 'fires every minute' },
      { path: 'inputs[1].key', message: 'Use lowercase' },
      { path: 'steps', message: 'Add at least one step' },
      { path: 'limits.max_parallel_agents', message: 'Too big' },
    ]);
    expect(m.steps).toEqual({ ok: ['question: Too small'], sum: ["brief: steps.ok.summary: 'ok' is not upstream of 'sum'"] });
    expect(m.edges).toEqual({ 0: ['route: fetch has no route x'] });
    expect(m.start).toEqual(['Schedule 1: cron: fires every minute', 'Input 2: key: Use lowercase']);
    expect(m.general).toEqual(['Add at least one step', 'limits.max_parallel_agents: Too big']);
    expect(m.count).toBe(7);
  });
});
