import type { AutomationDefinition, AutomationDetail, AutomationSummary, RunDetail, StepRunInfo } from '@desk/protocol';

/** Fetch pages (script, routes changed/unchanged) → Summarise (agent, on changed) → Publish? (Ask me). */
export const digestDef = (): AutomationDefinition => ({
  title: 'Weekly digest',
  description: 'Summarises what changed on competitors’ pages.',
  inputs: [{ key: 'topic', label: 'Topic', type: 'text', required: true }],
  triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'Europe/Paris', catch_up: 'once' }],
  steps: [
    { id: 'fetch', title: 'Fetch pages', kind: 'script', skill: 'digest', script: 'scripts/fetch.py', args: ['{{inputs.topic}}'], idempotent: false, join: 'all', on_error: 'stop', routes: ['changed', 'unchanged'], publish: [] },
    { id: 'sum', title: 'Summarise', kind: 'agent', brief: 'Summarise {{steps.fetch.dir}}.', skills: ['web-research'], output_keys: [{ key: 'headline', description: 'the biggest change' }], join: 'all', on_error: 'stop', routes: [], publish: ['digest.md'] },
    { id: 'ok', title: 'Publish?', kind: 'ask', question: 'Publish "{{steps.sum.outputs.headline}}"?', show: ['digest.md'], join: 'all', on_error: 'stop', routes: [], publish: [] },
  ],
  edges: [
    { from: 'fetch', to: 'sum', route: 'changed' },
    { from: 'sum', to: 'ok' },
  ],
  after_run: 'notify',
  limits: { run_deadline_hours: 24, max_parallel_agents: 2, max_parallel_scripts: 4 },
});

export const automationSummary = (over: Partial<AutomationSummary> = {}): AutomationSummary => ({
  id: 'a1',
  project_id: 'p',
  name: 'digest',
  title: 'Weekly digest',
  description: 'Summarises what changed on competitors’ pages.',
  version: 7,
  tested_version: 6,
  enabled: true,
  grants_suspended: false,
  schedules: [{ cron: '0 8 * * 1', timezone: 'Europe/Paris' }],
  last_run: null,
  next_due: '2026-10-05T06:00:00.000Z',
  enable_requested: false,
  updated_at: '2026-09-27T10:00:00.000Z',
  ...over,
});

export const automationDetail = (over: Partial<AutomationDetail> = {}): AutomationDetail => ({
  ...automationSummary(),
  definition: digestDef(),
  layout: {},
  grants: [],
  proposed_grants: [],
  grants_set_version: null,
  enable_request: null,
  ...over,
});

export const stepRun = (step_id: string, over: Partial<StepRunInfo> = {}): StepRunInfo => ({
  step_id,
  attempt: 1,
  status: 'pending',
  route: null,
  outputs: {},
  summary: null,
  error: null,
  agent_id: null,
  child_run_id: null,
  resume_at: null,
  gate: null,
  question: null,
  note: null,
  started_at: null,
  finished_at: null,
  ...over,
});

/** Run #14, six minutes in: Fetch pages done (route changed), Summarise running as agent ag1, Publish? not reached. */
export const runDetail = (over: Partial<RunDetail> = {}): RunDetail => ({
  id: 'r14',
  number: 14,
  automation_id: 'a1',
  project_id: 'p',
  version: 7,
  trigger: 'schedule',
  test: false,
  inputs: { topic: 'robots' },
  by: 'schedule',
  parent_run_id: null,
  parent_step_id: null,
  due_at: '2026-09-28T06:00:00.000Z',
  caught_up: 0,
  status: 'running',
  at_step: 'Summarise',
  summary: null,
  reason: null,
  started_at: '2026-09-28T06:00:00.000Z',
  finished_at: null,
  deadline_at: '2026-09-29T06:00:00.000Z',
  automation_name: 'digest',
  automation_title: 'Weekly digest',
  definition: digestDef(),
  steps: [
    stepRun('fetch', { status: 'succeeded', route: 'changed', summary: '3 of 5 sites differ', started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:00:12.000Z' }),
    stepRun('sum', { status: 'running', agent_id: 'ag1', started_at: '2026-09-28T06:00:12.000Z' }),
    stepRun('ok', { attempt: 0 }),
  ],
  ...over,
});
