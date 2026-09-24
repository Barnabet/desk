import { z } from 'zod';
import { ServiceName } from '@desk/protocol';
import { formatServiceLine } from '../coordination/render';
import { abortableSleep } from '../model/retry';
import { findService, getAgent, getService, listServices, type ServiceRow } from '../state/queries';
import { defineTool, type ToolContext } from './types';

/** How long service_start waits for a URL or an early exit before answering. */
export const SERVICE_START_WAIT_MS = 8000;

function requireService(ctx: ToolContext, name: string): ServiceRow {
  const s = findService(ctx.services.store.db, ctx.projectId, name);
  if (s) return s;
  const names = listServices(ctx.services.store.db, ctx.projectId).map((x) => x.name);
  throw new Error(`No service named "${name}". ${names.length ? `Services: ${names.join(', ')}` : 'This project has no services yet.'}`);
}

function threadTitle(ctx: ToolContext, id: string): string | null {
  return getAgent(ctx.services.store.db, id)?.title ?? null;
}

/** Waits until the run prints a URL, ends, or the wait is over; then reports its state and last log lines. */
async function report(ctx: ToolContext, s: ServiceRow, waitMs: number): Promise<string> {
  const deadline = Date.now() + waitMs;
  let row = s;
  while (row.status === 'running' && !row.url && Date.now() < deadline) {
    await abortableSleep(200, ctx.signal);
    row = getService(ctx.services.store.db, s.id) ?? row;
  }
  const logs = ctx.services.serviceLogs(row.id, 20).text;
  const hint = row.status === 'running' ? (row.url ? '' : ' No URL printed yet; check service_logs.') : ' It is not running; check the log below.';
  return `${formatServiceLine(row, threadTitle(ctx, row.agent_id))}${hint}\n\nLast log lines:\n${logs || '(no output yet)'}`;
}

export const serviceStartTool = defineTool({
  name: 'service_start',
  description:
    'Start a long-lived project service (dev server, API, worker) that keeps running after you finish, so the user can try the work; it shows in the Services panel. ' +
    'Runs sandboxed in a thread workspace: your own (threads), or thread_id (Desk, required). Starting an existing name restarts it with the new command. ' +
    'Waits up to 8 s for a URL or an early exit. Use bash_background instead for short-lived jobs that should stop with you.',
  input: z.object({
    name: ServiceName.describe('Short name, e.g. backend, frontend, worker'),
    command: z.string().min(1).describe('Shell command, e.g. "npm run dev -- --port 5173"'),
    cwd: z.string().optional().describe('Directory inside the workspace to run from (default: its root)'),
    thread_id: z.string().optional().describe("Desk only: the thread whose workspace to run in"),
  }),
  gate: { subject: (i) => ({ command: i.command }), unmatched: 'auto', alsoMatches: ['bash_background'] },
  async execute({ name, command, cwd, thread_id }, ctx) {
    const self = getAgent(ctx.services.store.db, ctx.agentId);
    let threadId: string;
    if (self?.role === 'desk') {
      if (!thread_id) throw new Error('Pass thread_id: the thread whose workspace the service runs in.');
      threadId = thread_id;
    } else {
      if (thread_id && thread_id !== ctx.agentId) throw new Error('Threads can only run services in their own workspace; leave thread_id out.');
      threadId = ctx.agentId;
    }
    const row = await ctx.services.startService(ctx.projectId, { name, command, ...(cwd ? { cwd } : {}), threadId, by: `agent:${ctx.agentId}` });
    return report(ctx, row, SERVICE_START_WAIT_MS);
  },
});

export const serviceLogsTool = defineTool({
  name: 'service_logs',
  description: "Read the end of a service's log (stdout and stderr of its runs).",
  input: z.object({ name: ServiceName, lines: z.number().int().min(1).max(400).optional() }),
  async execute({ name, lines }, ctx) {
    const s = requireService(ctx, name);
    const log = ctx.services.serviceLogs(s.id, lines ?? 80);
    return `${formatServiceLine(s, threadTitle(ctx, s.agent_id))}\n\n${log.truncated ? '[earlier lines omitted]\n' : ''}${log.text || '(no output)'}`;
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
    return formatServiceLine(row, threadTitle(ctx, row.agent_id));
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

export const serviceListTool = defineTool({
  name: 'service_list',
  description: "List the project's services with their status, URL and workspace.",
  input: z.object({}),
  async execute(_input, ctx) {
    const all = listServices(ctx.services.store.db, ctx.projectId);
    return all.length ? all.map((s) => formatServiceLine(s, threadTitle(ctx, s.agent_id))).join('\n') : 'No services yet. Start one with service_start.';
  },
});

export const serviceTools = [serviceStartTool, serviceLogsTool, serviceStopTool, serviceRestartTool, serviceListTool];
