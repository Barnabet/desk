import { z } from 'zod';
import { ServiceName } from '@desk/protocol';
import { formatServiceLine, servicePlace } from '../coordination/render';
import { abortableSleep } from '../model/retry';
import { findService, getAgent, getService, getSource, listServices, type ServiceRow } from '../state/queries';
import { defineTool, type ToolContext } from './types';

/** How long service_start waits for a URL or an early exit before answering. */
export const SERVICE_START_WAIT_MS = 8000;

function requireService(ctx: ToolContext, name: string): ServiceRow {
  const s = findService(ctx.services.store.db, ctx.projectId, name);
  if (s) return s;
  const names = listServices(ctx.services.store.db, ctx.projectId).map((x) => x.name);
  throw new Error(`No service named "${name}". ${names.length ? `Services: ${names.join(', ')}` : 'This project has no services yet.'}`);
}

function line(ctx: ToolContext, s: ServiceRow): string {
  const db = ctx.services.store.db;
  return formatServiceLine(s, servicePlace(s, s.source_id ? getSource(db, s.source_id) : null, getAgent(db, s.agent_id)?.title));
}

const ONE_OFF =
  'A service must keep running; for one-off commands use bash (threads) or run_check (Desk), so they do not fill the user\'s Services list.';

/**
 * Waits until the run prints a URL, ends, or the wait is over; then reports its state and last log lines. With `oneOff`
 * (a new name), a run that ended cleanly without serving anything was a one-off command: it is taken off the list.
 */
async function report(ctx: ToolContext, s: ServiceRow, waitMs: number, oneOff = false): Promise<string> {
  const deadline = Date.now() + waitMs;
  let row = s;
  while (row.status === 'running' && !row.url && Date.now() < deadline) {
    await abortableSleep(200, ctx.signal);
    row = getService(ctx.services.store.db, s.id) ?? row;
  }
  const logs = ctx.services.serviceLogs(row.id, 20).text;
  const tail = `\n\nLast log lines:\n${logs || '(no output yet)'}`;
  if (oneOff && row.status === 'exited' && row.exit_code === 0 && !row.url) {
    await ctx.services.removeService(row.id, `agent:${ctx.agentId}`);
    return `${row.name} ran and ended (exit 0) without serving anything, so it was not kept as a service. ${ONE_OFF}${tail}`;
  }
  const hint =
    row.status === 'running'
      ? row.url ? '' : ' No URL printed yet; check service_logs.'
      : ` It is not running; check the log below. If it was a one-off command, take it off the list with service_remove. ${ONE_OFF}`;
  return `${line(ctx, row)}${hint}${tail}`;
}

export const serviceStartTool = defineTool({
  name: 'service_start',
  description:
    'Start a long-lived project service (dev server, API, worker) that keeps running after you finish, so the user can try the work; it shows in the Services panel. ' +
    'Never use it for one-off commands (a cleanup, a git command, a script that ends): they would sit in the user\'s Services list. Threads run those with bash; Desk with run_check. ' +
    'Runs sandboxed either in a project source folder that allows agents to write (source_id; use this for the project\'s own tools and data, e.g. a local app that reads the real data) or in a thread workspace: your own (threads), or thread_id (Desk). ' +
    'Starting an existing name restarts it with the new command. ' +
    'Waits up to 8 s for a URL or an early exit. Use bash_background instead for jobs that should stop with you.',
  input: z.object({
    name: ServiceName.describe('Short name, e.g. backend, frontend, worker'),
    command: z.string().min(1).describe('Shell command, e.g. "npm run dev -- --port 5173"'),
    cwd: z.string().optional().describe('Directory inside the workspace to run from (default: its root)'),
    thread_id: z.string().optional().describe("Desk: the thread whose workspace to run in"),
    source_id: z.string().optional().describe('Run in this project source folder instead of a workspace'),
  }),
  gate: { subject: (i) => ({ command: i.command }), unmatched: 'auto', alsoMatches: ['bash_background'] },
  async execute({ name, command, cwd, thread_id, source_id }, ctx) {
    const by = `agent:${ctx.agentId}`;
    const opts = { name, command, ...(cwd ? { cwd } : {}), by };
    const isNew = !findService(ctx.services.store.db, ctx.projectId, name);
    if (source_id) {
      if (thread_id) throw new Error('Pass either source_id or thread_id, not both.');
      return report(ctx, await ctx.services.startService(ctx.projectId, { ...opts, sourceId: source_id }), SERVICE_START_WAIT_MS, isNew);
    }
    const self = getAgent(ctx.services.store.db, ctx.agentId);
    let threadId: string;
    if (self?.role === 'desk') {
      if (!thread_id) throw new Error('Pass source_id (a project source folder) or thread_id (a thread workspace) to run the service in.');
      threadId = thread_id;
    } else {
      if (thread_id && thread_id !== ctx.agentId) throw new Error('Threads can only run services in their own workspace or a project source (source_id); leave thread_id out.');
      threadId = ctx.agentId;
    }
    const row = await ctx.services.startService(ctx.projectId, { ...opts, threadId });
    return report(ctx, row, SERVICE_START_WAIT_MS, isNew);
  },
});

export const serviceLogsTool = defineTool({
  name: 'service_logs',
  description: "Read the end of a service's log (stdout and stderr of its runs).",
  input: z.object({ name: ServiceName, lines: z.number().int().min(1).max(400).optional() }),
  async execute({ name, lines }, ctx) {
    const s = requireService(ctx, name);
    const log = ctx.services.serviceLogs(s.id, lines ?? 80);
    return `${line(ctx, s)}\n\n${log.truncated ? '[earlier lines omitted]\n' : ''}${log.text || '(no output)'}`;
  },
});

export const serviceStopTool = defineTool({
  name: 'service_stop',
  description: 'Stop a running service.',
  input: z.object({ name: ServiceName }),
  async execute({ name }, ctx) {
    const s = requireService(ctx, name);
    if (s.status !== 'running') return `${name} is not running (${s.status}).`;
    const row = await ctx.services.stopService(s.id, `agent:${ctx.agentId}`);
    return line(ctx, row);
  },
});

export const serviceRestartTool = defineTool({
  name: 'service_restart',
  description: "Run a service's recorded command again in its recorded workspace (e.g. after changing code or config it does not reload).",
  input: z.object({ name: ServiceName }),
  async execute({ name }, ctx) {
    const s = requireService(ctx, name);
    const row = await ctx.services.restartService(s.id, `agent:${ctx.agentId}`);
    return report(ctx, row, SERVICE_START_WAIT_MS);
  },
});

export const serviceRemoveTool = defineTool({
  name: 'service_remove',
  description: "Take a service that is not running (exited or stopped) off the project's Services list, with its log. Use it for services that are no longer needed, such as a one-off command started by mistake.",
  input: z.object({ name: ServiceName }),
  async execute({ name }, ctx) {
    const s = requireService(ctx, name);
    if (s.status === 'running') throw new Error(`${name} is running; stop it first with service_stop.`);
    await ctx.services.removeService(s.id, `agent:${ctx.agentId}`);
    return `Removed ${name} from the Services list.`;
  },
});

export const serviceListTool = defineTool({
  name: 'service_list',
  description: "List the project's services with their status, URL and workspace.",
  input: z.object({}),
  async execute(_input, ctx) {
    const all = listServices(ctx.services.store.db, ctx.projectId);
    return all.length ? all.map((s) => line(ctx, s)).join('\n') : 'No services yet. Start one with service_start.';
  },
});

export const serviceTools = [serviceStartTool, serviceLogsTool, serviceStopTool, serviceRestartTool, serviceRemoveTool, serviceListTool];
