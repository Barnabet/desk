// packages/core/src/tools/automations.ts
import { z } from 'zod';
import { InputKey, InputValue, type AutomationDefinition, type Step } from '@desk/protocol';
import { getRun, getStepRun, listRuns, type AutomationRow, type AutomationRunRow } from '../automations/queries';
import { automationSummary } from '../automations/views';
import { renderTranscript } from '../coordination/render';
import { ValidationError } from '../errors';
import { getAgent, lastEvent } from '../state/queries';
import { defineTool, type ToolContext } from './types';

const TRANSCRIPT_MAX = 40_000;

const Inputs = z.record(InputKey, InputValue).default({}).describe('Input values by key; defaults apply to the ones you leave out');

function requireAutomation(ctx: ToolContext, name: string): AutomationRow {
  try {
    return ctx.services.automations.requireByName(ctx.projectId, name);
  } catch {
    throw new Error(`Unknown automation: ${name}. automation_list shows this project's automations.`);
  }
}

function requireRun(ctx: ToolContext, runId: string): AutomationRunRow {
  const run = getRun(ctx.services.store.db, runId);
  if (!run || run.project_id !== ctx.projectId) throw new Error(`Unknown run: ${runId}`);
  return run;
}

const schedules = (def: AutomationDefinition) => (def.triggers.length ? def.triggers.map((t) => `${t.cron} (${t.timezone})`).join('; ') : 'none (Run now only)');

/** Rethrows a validation failure with every problem on its own line, for the model to fix in one go. */
function explain(e: unknown): never {
  if (e instanceof ValidationError) {
    const errors = (e.details as { errors?: Array<{ path: string; message: string }> } | undefined)?.errors;
    if (errors?.length) throw new Error(`The automation is not valid:\n${errors.map((i) => `- ${i.path || '(definition)'}: ${i.message}`).join('\n')}`);
  }
  throw e;
}

/** What a run of `def` really does, one line per effect, for automation_test (spec §6.1). */
export function whatItDoes(name: string, def: AutomationDefinition): string[] {
  const line = (s: Step): string => {
    switch (s.kind) {
      case 'script':
        return `- ${s.title}: runs ${s.skill}/${s.script} for real with DESK_TEST=1 set; it acts outward unless the script checks DESK_TEST`;
      case 'agent':
        return `- ${s.title}: an agent works with its tools (web, files in its folder${s.git_source_id ? ', git on its own branch' : ''}); its approvals go to the user`;
      case 'ask':
        return `- ${s.title}: waits for the user's answer in the app`;
      case 'automation':
        return `- ${s.title}: runs "${s.automation}" as a test run too`;
      case 'wait':
        return `- ${s.title}: really waits ${s.minutes !== undefined ? `${s.minutes} min` : `until ${s.until}`}`;
      case 'tell_desk':
        return `- ${s.title}: sends you a message`;
    }
  };
  const lines = def.steps.map(line);
  if (def.steps.some((s) => s.publish.length)) lines.push(`- Published files go to the library under automations/${name}/tests/`);
  return lines;
}

export const automationListTool = defineTool({
  name: 'automation_list',
  description: "List this project's automations: switch, version and tested version, schedules, last run, next time, flags.",
  input: z.object({}),
  async execute(_input, ctx) {
    const { automations, engine, store } = ctx.services;
    const rows = automations.list(ctx.projectId);
    if (!rows.length) return 'No automations yet.';
    return rows
      .map((row) => {
        const s = automationSummary(store.db, row, engine.now());
        const tested = s.tested_version === null ? 'not tested' : s.tested_version === s.version ? 'tested' : `tested in v${s.tested_version}, untested changes`;
        const last = s.last_run ? `${s.last_run.status}${s.last_run.test ? ' (test)' : ''} ${s.last_run.started_at}${s.last_run.waiting_on ? `, waiting on ${s.last_run.waiting_on}` : ''}` : 'none';
        const flags = [...(s.grants_suspended ? ['grants suspended'] : []), ...(s.enable_requested ? ['turn-on requested'] : [])];
        return `- ${s.name} ${JSON.stringify(s.title)}: ${s.enabled ? 'on' : 'off'} · v${s.version} (${tested}) · schedules: ${schedules(row.definition)} · last run: ${last}${s.next_due ? ` · next: ${s.next_due}` : ''}${flags.length ? ` · ${flags.join(', ')}` : ''}`;
      })
      .join('\n');
  },
});

export const automationReadTool = defineTool({
  name: 'automation_read',
  description: 'Read an automation: its definition as JSON (the current version, or `version`), switch, grants and last 5 runs.',
  input: z.object({ name: z.string(), version: z.number().int().min(1).optional() }),
  async execute({ name, version }, ctx) {
    const { automations, engine, store } = ctx.services;
    const a = requireAutomation(ctx, name);
    const def = version ? automations.version(a.id, version).definition : a.definition;
    const s = automationSummary(store.db, a, engine.now());
    const tested = s.tested_version === null ? 'not tested' : `tested in v${s.tested_version}`;
    const runs = listRuns(store.db, a.id, { limit: 5 });
    return [
      `${a.name} v${version ?? a.version}${version && version !== a.version ? ` (current v${a.version})` : ''} · ${a.enabled ? 'on' : 'off'} · grants: ${a.grants.length}${a.grants_suspended ? ' (suspended until the user keeps them)' : ''} · ${tested}`,
      'Definition:',
      JSON.stringify(def, null, 2),
      runs.length ? `Last runs:\n${runs.map((r) => `- ${r.id} ${r.status} ${r.trigger}${r.test ? ' (test)' : ''} ${r.started_at}${r.summary ? `: ${r.summary}` : ''}`).join('\n')}` : 'Last runs: none',
    ].join('\n');
  },
});

export const automationSaveTool = defineTool({
  name: 'automation_save',
  description:
    'Create an automation (a new name, no base_version) or save a new version of one (base_version = the version you read). Validates everything and lists every error.',
  input: z.object({
    name: z.string().describe('Lowercase letters, digits and dashes; fixed at creation'),
    definition: z.record(z.string(), z.unknown()).describe('The AutomationDefinition: title, description, inputs, triggers, steps, edges, output_step, after_run, limits'),
    change_note: z.string().min(1).describe('What changed and why, for the version history'),
    base_version: z.number().int().min(1).optional(),
  }),
  async execute({ name, definition, change_note, base_version }, ctx) {
    const { automations } = ctx.services;
    const meta = { origin: `agent:${ctx.agentId}`, changeNote: change_note, via: 'tool' as const };
    let existing: AutomationRow | undefined;
    try {
      existing = automations.requireByName(ctx.projectId, name);
    } catch {
      existing = undefined;
    }
    let result;
    try {
      if (!existing) {
        if (base_version !== undefined) throw new Error(`There is no automation named ${name}; leave base_version out to create it.`);
        result = automations.create(ctx.projectId, name, definition, meta);
      } else {
        if (base_version === undefined) {
          throw new Error(`${name} exists (v${existing.version}). Read it with automation_read, then save with base_version ${existing.version} to update it.`);
        }
        result = automations.save(existing.id, definition, { ...meta, baseVersion: base_version });
      }
    } catch (e) {
      explain(e);
    }
    const { automation, warnings } = result;
    return [
      `Saved ${automation.name} v${automation.version}.`,
      ...(warnings.length ? [`Warnings:\n${warnings.map((w) => `- ${w.path || '(definition)'}: ${w.message}`).join('\n')}`] : []),
      ...(automation.grants_suspended && automation.grants.length ? ['Its grants are suspended until the user keeps them: runs ask again meanwhile.'] : []),
      'Test it with automation_test before proposing it.',
    ].join('\n');
  },
});

export const automationTestTool = defineTool({
  name: 'automation_test',
  description: 'Start a test run of the current version with realistic inputs. Returns the run id and what the run will really do; then call wait_for_run.',
  input: z.object({ name: z.string(), inputs: Inputs }),
  async execute({ name, inputs }, ctx) {
    const a = requireAutomation(ctx, name);
    const runId = await ctx.services.engine.startRun(a.id, { trigger: 'test', test: true, inputs, by: `agent:${ctx.agentId}` });
    return [
      `Started test run ${runId} of ${a.name} v${a.version}. What it really does:`,
      ...whatItDoes(a.name, a.definition),
      `Call wait_for_run("${runId}") to get its report. Tell the user what this test does to the outside world.`,
    ].join('\n');
  },
});

export const automationRunTool = defineTool({
  name: 'automation_run',
  description: 'Run an automation now for real (not a test). Only when the user asked for a run.',
  input: z.object({ name: z.string(), inputs: Inputs }),
  async execute({ name, inputs }, ctx) {
    const a = requireAutomation(ctx, name);
    const runId = await ctx.services.engine.startRun(a.id, { trigger: 'desk', test: false, inputs, by: `agent:${ctx.agentId}` });
    return `Started run ${runId} of ${a.name} v${a.version}. You'll get its report when it ends; wait_for_run("${runId}") pauses until then.`;
  },
});

export const waitForRunTool = defineTool({
  name: 'wait_for_run',
  description: 'Pause until an automation run ends (you get its report) or starts waiting on the user (you get a notice).',
  input: z.object({ run_id: z.string() }),
  async execute({ run_id }, ctx) {
    const run = requireRun(ctx, run_id);
    if (run.status !== 'running') return `Run ${run.id} already ended.\n\n${ctx.services.engine.report(run.id)}`;
    ctx.services.engine.watch(run.id);
    return { content: `Waiting for run ${run.id}.`, yield: { status: 'waiting', reason: `Waiting for automation run ${run.id}` } };
  },
});

export const automationReadRunTool = defineTool({
  name: 'automation_read_run',
  description:
    "Read a run's report, or one step: an agent step's summary or `full` transcript, a script's log tail, a sub-automation's child run report.",
  input: z.object({ run_id: z.string(), step: z.string().optional(), mode: z.enum(['summary', 'full']).default('summary') }),
  async execute({ run_id, step, mode }, ctx) {
    const { engine, store } = ctx.services;
    const run = requireRun(ctx, run_id);
    if (!step) return engine.report(run.id);
    const def = engine.definitionOf(run);
    const s = def.steps.find((x) => x.id === step);
    if (!s) throw new Error(`Run ${run.id} has no step ${step} (steps: ${def.steps.map((x) => x.id).join(', ')})`);
    const row = getStepRun(store.db, run.id, step);
    const head = [
      `${s.title} [${s.kind}] — ${row?.status ?? 'pending'}${row && row.attempt > 1 ? ` (attempt ${row.attempt})` : ''}${row?.route ? ` · route ${row.route}` : ''}`,
      ...(row?.summary ? [`Summary: ${row.summary}`] : []),
      ...(row && Object.keys(row.outputs).length ? [`Outputs: ${JSON.stringify(row.outputs)}`] : []),
      ...(row?.error ? [`Error: ${row.error}`] : []),
    ];
    if (s.kind === 'script') {
      const log = engine.stepLog(run.id, step);
      return [...head, `Log: ${log.path}`, log.tail || '(empty)'].join('\n');
    }
    if (s.kind === 'automation' && row?.child_run_id && getRun(store.db, row.child_run_id)) return [...head, '', engine.report(row.child_run_id)].join('\n');
    if (s.kind === 'agent' && row?.agent_id) {
      const agent = getAgent(store.db, row.agent_id);
      if (mode === 'full') {
        const t = renderTranscript(store.list({ agentId: row.agent_id }));
        return [...head, `Agent ${row.agent_id} transcript:`, t.length > TRANSCRIPT_MAX ? `…[earlier events omitted]\n${t.slice(-TRANSCRIPT_MAX)}` : t].join('\n');
      }
      const last = lastEvent(store.db, row.agent_id, 'assistant.message');
      const said = last?.type === 'assistant.message' ? last.payload.content : null;
      return [...head, `Agent ${row.agent_id}: ${agent?.status ?? 'gone'}`, ...(said ? [`Last message: ${said.slice(0, 4000)}`] : []), 'mode "full" shows its transcript.'].join('\n');
    }
    return head.join('\n');
  },
});

export const automationCancelTool = defineTool({
  name: 'automation_cancel',
  description: 'Cancel a running automation run (its agents stop, scripts are killed, child runs are cancelled).',
  input: z.object({ run_id: z.string(), reason: z.string().min(1) }),
  async execute({ run_id, reason }, ctx) {
    const run = requireRun(ctx, run_id);
    await ctx.services.engine.cancelRun(run.id, reason);
    return `Cancelled run ${run.id}.`;
  },
});

export const automationRequestEnableTool = defineTool({
  name: 'automation_request_enable',
  description: 'Ask the user to turn an automation on: they see your note, its schedules and the grants its test runs needed. Only the user turns it on.',
  input: z.object({ name: z.string(), note: z.string().min(1).describe('What it does, what the test showed, why it is ready') }),
  async execute({ name, note }, ctx) {
    const { automations, engine, store } = ctx.services;
    const a = requireAutomation(ctx, name);
    automations.requestEnable(a.id, ctx.agentId, note);
    const after = automations.require(a.id);
    const grants = after.enable_request?.proposed_grants ?? [];
    const tested = automationSummary(store.db, after, engine.now()).tested_version === after.version;
    return [
      `Asked the user to turn on ${a.name} (schedules: ${schedules(a.definition)}), with ${grants.length} proposed grant(s)${grants.length ? `: ${grants.map((g) => `${g.tool}${g.match ? ` ${JSON.stringify(g.match)}` : ''}`).join(', ')}` : ''}.`,
      ...(tested ? [] : [`Warning: v${after.version} has no succeeded test run; the user will see that.`]),
      "Only the user can turn it on; don't say it is on until automation_list shows it.",
    ].join('\n');
  },
});

export const automationDeleteTool = defineTool({
  name: 'automation_delete',
  description: 'Delete an automation (its runs are cancelled; its history is kept). Needs approval.',
  input: z.object({ name: z.string() }),
  gate: { subject: () => ({}), unmatched: 'ask' },
  async execute({ name }, ctx) {
    const a = requireAutomation(ctx, name);
    await ctx.services.automations.delete(a.id, `agent:${ctx.agentId}`);
    return `Deleted automation ${a.name}.`;
  },
});

/** Desk only (spec §6.1): threads and step agents get none. */
export const deskAutomationTools = [
  automationListTool,
  automationReadTool,
  automationSaveTool,
  automationTestTool,
  automationRunTool,
  waitForRunTool,
  automationReadRunTool,
  automationCancelTool,
  automationRequestEnableTool,
  automationDeleteTool,
];
