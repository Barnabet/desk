import { describe, expect, it } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { digestDef } from './testing/automations';
import { diffDefinitions } from './automation-diff';

describe('diffDefinitions', () => {
  it('lists steps, edges, inputs, schedules and settings that changed', () => {
    const a = digestDef();
    const b: AutomationDefinition = {
      ...a,
      title: 'Digest',
      limits: { ...a.limits, max_parallel_agents: 3 },
      inputs: [{ ...a.inputs[0]!, label: 'Subject' }, { key: 'deep', label: 'Go deep', type: 'boolean', required: false }],
      triggers: [{ ...a.triggers[0]!, cron: '0 9 * * 1' }],
      steps: [
        a.steps[0]!,
        { ...a.steps[1]!, brief: 'Summarise it.', skills: [] } as AutomationDefinition['steps'][number],
        { id: 'tell', title: 'Tell Desk', kind: 'tell_desk', text: 'Done: {{steps.sum.summary}}', attach: [], join: 'all', on_error: 'stop', routes: [], publish: [] },
      ],
      edges: [a.edges[0]!, { from: 'sum', to: 'tell' }],
    };
    const d = diffDefinitions(a, b, 'UTC');
    expect(d.steps).toEqual([
      { id: 'sum', title: 'Summarise', kind: 'agent', change: 'changed', fields: [{ field: 'brief', before: 'Summarise {{steps.fetch.dir}}.', after: 'Summarise it.' }, { field: 'skills', before: 'web-research', after: '—' }] },
      { id: 'ok', title: 'Publish?', kind: 'ask', change: 'removed', fields: [] },
      { id: 'tell', title: 'Tell Desk', kind: 'tell_desk', change: 'added', fields: [] },
    ]);
    expect(d.edges).toEqual({ added: ['Summarise → Tell Desk'], removed: ['Summarise → Publish?'] });
    expect(d.inputs).toEqual([
      { key: 'topic', label: 'Subject', change: 'changed', fields: [{ field: 'label', before: 'Topic', after: 'Subject' }] },
      { key: 'deep', label: 'Go deep', change: 'added', fields: [] },
    ]);
    expect(d.schedules).toEqual({ before: ['Mondays 08:00 (Europe/Paris)'], after: ['Mondays 09:00 (Europe/Paris)'] });
    expect(d.settings).toEqual([
      { field: 'title', before: 'Weekly digest', after: 'Digest' },
      { field: 'limits.max_parallel_agents', before: '2', after: '3' },
    ]);
    expect(d.empty).toBe(false);
  });

  it('finds nothing between equal definitions, whatever their key order', () => {
    const a = digestDef();
    const reordered = JSON.parse(JSON.stringify({ ...a, steps: a.steps.map((s) => Object.fromEntries(Object.entries(s).reverse())) })) as AutomationDefinition;
    expect(diffDefinitions(a, reordered).empty).toBe(true);
    expect(diffDefinitions(a, { ...a, edges: [{ ...a.edges[0]!, when: 'inputs.topic != ""' }, a.edges[1]!] }).edges).toEqual({
      added: ['Fetch pages → Summarise on changed if inputs.topic != ""'],
      removed: ['Fetch pages → Summarise on changed'],
    });
  });
});
