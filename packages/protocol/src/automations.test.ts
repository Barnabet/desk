import { describe, expect, it } from 'vitest';
import {
  AutomationDefinition,
  AutomationExport,
  Grant,
  Outputs,
  Step,
  StepOutputFile,
  outputsSize,
  OUTPUTS_MAX_BYTES,
} from './index';

const digest = {
  title: 'Weekly competitor digest',
  inputs: [{ key: 'sources', label: 'Sources file', type: 'file', required: true }],
  triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'Europe/Paris' }],
  steps: [
    { id: 'fetch', title: 'Fetch pages', kind: 'script', skill: 'competitor-digest', script: 'fetch.py', args: ['--urls', '{{inputs.sources}}'], routes: ['changed', 'unchanged'] },
    { id: 'summarise', title: 'Summarise changes', kind: 'agent', brief: 'Summarise {{steps.fetch.dir}}', skills: ['web-research'], output_keys: [{ key: 'headline', description: 'the biggest change' }] },
    { id: 'publish_ok', title: 'Publish?', kind: 'ask', question: 'Publish this digest?', show: ['digest.md'] },
    { id: 'pause', title: 'Wait', kind: 'wait', minutes: 30 },
    { id: 'tell', title: 'Tell Desk', kind: 'tell_desk', text: 'Rejected: {{steps.publish_ok.summary}}' },
    { id: 'sub', title: 'Archive', kind: 'automation', automation: 'archive-digest', inputs: { file: '{{steps.summarise.dir}}/digest.md' } },
  ],
  edges: [
    { from: 'fetch', to: 'summarise', route: 'changed' },
    { from: 'summarise', to: 'publish_ok' },
    { from: 'publish_ok', to: 'tell', route: 'rejected' },
    { from: 'publish_ok', to: 'pause', when: 'steps.summarise.outputs.headline != null' },
    { from: 'pause', to: 'sub' },
  ],
};

describe('automation definitions', () => {
  it('parses a full definition and fills every default', () => {
    const d = AutomationDefinition.parse(digest);
    expect(d.after_run).toBe('notify');
    expect(d.limits).toEqual({ run_deadline_hours: 24, max_parallel_agents: 2, max_parallel_scripts: 4 });
    expect(d.description).toBe('');
    expect(d.triggers[0]).toMatchObject({ catch_up: 'once' });
    const fetch = d.steps[0]!;
    expect(fetch).toMatchObject({ join: 'all', on_error: 'stop', publish: [], idempotent: false });
    expect(d.steps[1]).toMatchObject({ kind: 'agent', skills: ['web-research'] });
  });

  it('refuses reserved routes in a step and bad names', () => {
    const bad = (patch: object) => Step.safeParse({ id: 'a', title: 'A', kind: 'wait', minutes: 1, ...patch }).success;
    expect(bad({ routes: ['error'] })).toBe(false);
    expect(bad({ routes: ['rejected'] })).toBe(false);
    expect(bad({ id: 'Fetch' })).toBe(false);
    expect(bad({ routes: ['done'] })).toBe(true);
  });

  it('needs exactly one of minutes and until on a wait', () => {
    expect(Step.safeParse({ id: 'w', title: 'W', kind: 'wait' }).success).toBe(false);
    expect(Step.safeParse({ id: 'w', title: 'W', kind: 'wait', minutes: 5, until: '08:00' }).success).toBe(false);
    expect(Step.safeParse({ id: 'w', title: 'W', kind: 'wait', until: '08:00' }).success).toBe(true);
    expect(Step.safeParse({ id: 'w', title: 'W', kind: 'wait', until: '24:00' }).success).toBe(false);
    expect(Step.safeParse({ id: 'w', title: 'W', kind: 'wait', minutes: 10_081 }).success).toBe(false);
  });

  it('bounds outputs', () => {
    expect(Outputs.safeParse({ count: 3, ok: true, names: ['a', 'b'], none: null }).success).toBe(true);
    expect(Outputs.safeParse({ Bad: 1 }).success).toBe(false);
    expect(Outputs.safeParse({ s: 'x'.repeat(4001) }).success).toBe(false);
    expect(Outputs.safeParse({ l: Array.from({ length: 201 }, () => 1) }).success).toBe(false);
    expect(Outputs.safeParse({ nested: { a: 1 } }).success).toBe(false);
    const big = Object.fromEntries(Array.from({ length: 5 }, (_, i) => [`k${i}`, 'x'.repeat(3999)]));
    expect(outputsSize(big)).toBeGreaterThan(OUTPUTS_MAX_BYTES);
    expect(Outputs.safeParse(big).success).toBe(false);
    expect(StepOutputFile.parse({ route: 'changed', summary: 'ok' })).toEqual({ route: 'changed', summary: 'ok' });
  });

  it('parses grants and the export format', () => {
    expect(Grant.parse({ tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' }).tool).toBe('web_fetch');
    expect(Grant.safeParse({ tool: 'web_fetch', action: 'ask' }).success).toBe(false);
    const exp = AutomationExport.parse({ format: 'desk-automation/1', name: 'weekly-digest', definition: digest });
    expect(exp.definition.steps).toHaveLength(6);
    expect(AutomationExport.safeParse({ format: 'desk-automation/2', name: 'x', definition: digest }).success).toBe(false);
  });
});
