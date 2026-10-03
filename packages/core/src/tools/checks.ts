import { z } from 'zod';
import { CheckWhere, sanitizeLabel } from '@desk/protocol';
import { CHECK_TIMEOUT_S, MAX_CHECK_TIMEOUT_S, OUT_PREFIX } from '../checks/runner';
import { formatCheckLine, listChecks, listReceipts, formatReceiptLine, type ReceiptRow } from '../receipts/receipts';
import { getAgent } from '../state/queries';
import { requireThread } from './desk';
import { defineTool, type ToolContext } from './types';

/** Who ran a receipt's command, as list_receipts names it. */
function whoRan(ctx: ToolContext, r: ReceiptRow, names: Map<string, string>): string {
  if (r.check_id) return `check ${r.check_id} step ${r.step ?? '?'}`;
  let n = names.get(r.agent_id);
  if (n === undefined) {
    const a = getAgent(ctx.services.store.db, r.agent_id);
    n = !a ? r.agent_id : a.role === 'desk' ? 'Desk' : `"${sanitizeLabel(a.title ?? 'untitled')}" (${a.id})`;
    names.set(r.agent_id, n);
  }
  return n;
}

export const listReceiptsTool = defineTool({
  name: 'list_receipts',
  description:
    "What commands actually ran and how they ended, recorded by Desk itself (not what an agent says): each bash, bash_readonly, skill_run, background job and check step, with its git commit (+changes when tracked files had uncommitted edits), exit and duration, newest first. Filter by thread, commit (prefix), command text or failures. Also lists your recent check jobs. Use it to see whether a thread's tests really ran on the commit it submitted.",
  input: z.object({
    thread_id: z.string().optional().describe('Only this thread (id or exact title)'),
    commit: z.string().min(4).optional().describe('Only commands run at this commit (a prefix of the hash)'),
    command: z.string().min(1).optional().describe('Only commands containing this text'),
    failed_only: z.boolean().default(false),
    limit: z.number().int().min(1).max(200).default(30),
  }),
  async execute({ thread_id, commit, command, failed_only, limit }, ctx) {
    const db = ctx.services.store.db;
    const t = thread_id ? requireThread(ctx, thread_id) : undefined;
    const rows = listReceipts(db, ctx.projectId, { ...(t ? { agentIds: [t.id] } : {}), ...(commit ? { commit } : {}), ...(command ? { command } : {}), failedOnly: failed_only, limit });
    const names = new Map<string, string>();
    const out = [rows.length ? `Commands (newest first):\n${rows.map((r) => formatReceiptLine(r, whoRan(ctx, r, names))).join('\n')}` : 'No commands match.'];
    if (!t && !commit && !command) {
      const checks = listChecks(db, ctx.projectId, 10);
      if (checks.length) out.push(`Your recent checks:\n${checks.map(formatCheckLine).join('\n')}`);
    }
    return out.join('\n\n');
  },
});

export const runCheckTool = defineTool({
  name: 'run_check',
  description: [
    'Run a few shell commands in the background without a thread, to check or do something cheaply: run a test suite on what a thread submitted, rebuild a report, verify a file, or a one-off fix or cleanup (git worktree unlock, removing stale files). This, not service_start, is how you run one-off commands. No model runs; you get a Check message when it ends (passed, failed, timed out) with each step\'s exit and the failing step\'s last lines, so do not wait or poll.',
    'Steps run in order with zsh, sandboxed like bash, and stop at the first non-zero exit. Each leaves a receipt (list_receipts).',
    'where: "scratch" (default, an empty folder); {thread_id, mode: "snapshot"} (a clean git checkout of the thread\'s latest submitted commit, else its branch; writable, the thread\'s own workspace is not touched); {thread_id, mode: "workspace"} (the thread\'s workspace as it is, read-only); {source_id} (a project source, read-only). Add write: true to change the workspace (of a thread that is not working; its repo\'s shared git dir too, so git worktree commands work) or the source (one that allows agents to write).',
    `$DESK_CHECK_OUT is a folder the steps can always write, and that you can read afterwards. expect: files the steps must produce (relative to the working folder, or "${OUT_PREFIX}name"); each must exist, be non-empty and be written by this check, or the check fails.`,
    `At most 3 checks run at once per project. timeout_s covers all steps (default ${CHECK_TIMEOUT_S}).`,
  ].join(' '),
  input: z.object({
    title: z.string().trim().min(1).max(120).describe('What it checks, in a few words'),
    steps: z.array(z.string().min(1)).min(1).max(20),
    where: CheckWhere.default('scratch'),
    expect: z.array(z.string().min(1)).max(20).default([]),
    timeout_s: z.number().int().min(1).max(MAX_CHECK_TIMEOUT_S).default(CHECK_TIMEOUT_S),
  }),
  // Each step is checked like a bash command: a rule that asks before a command asks before a check that runs it.
  gate: { subject: (i) => ({ command: i.steps.join('\n') }), subjects: (i) => i.steps.map((command) => ({ command })), unmatched: 'auto', alsoMatches: ['bash'] },
  async execute({ title, steps, where, expect, timeout_s }, ctx) {
    const c = await ctx.services.checks.start(ctx.agentId, { title, steps, where, expect, timeoutS: timeout_s });
    return `Started check ${c.id} "${sanitizeLabel(c.title)}" in ${c.cwd}${c.head ? ` at commit ${c.head.slice(0, 10)}` : ''} (${c.steps.length} step${c.steps.length === 1 ? '' : 's'}, timeout ${c.timeout_s} s). You will get a Check message when it ends; meanwhile carry on.`;
  },
});

export const checkLogTool = defineTool({
  name: 'check_log',
  description: "Read the end of a check step's log (stdout and stderr). Without step: the failing step, else the last one.",
  input: z.object({ check_id: z.string(), step: z.number().int().min(1).optional(), lines: z.number().int().min(1).max(2000).default(100) }),
  async execute({ check_id, step, lines }, ctx) {
    return ctx.services.checks.log(ctx.agentId, check_id, step, lines);
  },
});

export const cancelCheckTool = defineTool({
  name: 'cancel_check',
  description: 'Stop a running check. It ends as cancelled.',
  input: z.object({ check_id: z.string() }),
  async execute({ check_id }, ctx) {
    const c = ctx.services.checks.cancel(ctx.agentId, check_id);
    return `Cancelling ${c.id}; you will get its Check message once its current step has stopped.`;
  },
});

export const deskCheckTools = [listReceiptsTool, runCheckTool, checkLogTool, cancelCheckTool];
