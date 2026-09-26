import { and, asc, eq, sql } from 'drizzle-orm';
import { Grant, type EventOf, type PolicyRule } from '@desk/protocol';
import type { Db } from '../db/open';
import { agents, approvals, automationRuns, events } from '../db/schema';
import type { PolicySubject, Tool } from '../tools/types';
import { getAutomation } from './queries';

// Grants (spec 2026-09-26-automations-design §5.3): user-set rules for one automation's runs, checked before the project policy.

/** `text` with regex metacharacters escaped, so the pattern matches only that text. */
export function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * The grant "Approve and remember" records for a call (spec §5.3 table). Always `allow`. A shell call without a
 * command gets `^$`, which matches nothing real, rather than a grant for every command.
 */
export function deriveGrant(tool: string, subject: PolicySubject, automationName: string): Grant {
  switch (tool) {
    case 'web_fetch':
    case 'web_search':
      return subject.domain ? { tool, match: { domain: subject.domain }, action: 'allow' } : { tool, action: 'allow' };
    case 'git_push':
    case 'open_pr':
      return { tool, match: { branch: `desk/auto-${automationName}-*` }, action: 'allow' };
    case 'skill_run': {
      // That installed script with any arguments: templated arguments change from run to run.
      const script = (subject.command ?? '').trim().split(/\s+/)[0] ?? '';
      return { tool, match: { command: script ? `^${escapeRegExp(script)}(\\s|$)` : '^$' }, action: 'allow' };
    }
    case 'bash':
    case 'bash_background':
      return { tool, match: { command: `^${escapeRegExp(subject.command ?? '')}$` }, action: 'allow' };
    default:
      return { tool, action: 'allow' };
  }
}

const SECOND_LEVEL = new Set(['co', 'com', 'net', 'org', 'gov', 'ac', 'edu']);

/**
 * The widening the Grants tab offers for a domain grant: the registrable domain and its subdomains, e.g.
 * `news.bbc.co.uk` → `['bbc.co.uk', '*.bbc.co.uk']` (a glob `*.x` does not match `x` itself). IP addresses, single
 * labels and bare public suffixes such as `co.uk` stay as they are.
 */
export function widenDomain(host: string): string[] {
  const h = host.toLowerCase().replace(/\.$/, '');
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(h) || h.includes(':') || !h.includes('.')) return [h];
  const labels = h.split('.');
  const tld = labels.at(-1) ?? '';
  const second = labels.at(-2) ?? '';
  const n = tld.length === 2 && SECOND_LEVEL.has(second) ? 3 : 2;
  if (labels.length < n) return [h];
  const registrable = labels.slice(-n).join('.');
  return [registrable, `*.${registrable}`];
}

/** Grants as policy rules, to go before the project's. Grants never carry `delegate_to_desk`: step agents ask the user. */
export function grantRules(grants: Grant[]): PolicyRule[] {
  return grants.map((g): PolicyRule => (g.match ? { tool: g.tool, match: { ...g.match }, action: g.action } : { tool: g.tool, action: g.action }));
}

/** Same tool, action and match (a missing field and an empty match are the same). */
export function sameGrant(a: Grant, b: Grant): boolean {
  return a.tool === b.tool && a.action === b.action && a.match?.branch === b.match?.branch && a.match?.command === b.match?.command && a.match?.domain === b.match?.domain;
}

/** `list` with `g` appended, unless an equal grant is already there. */
export function addGrant(list: Grant[], g: Grant): Grant[] {
  return list.some((x) => sameGrant(x, g)) ? list : [...list, g];
}

/**
 * What the Turn-on dialog proposes: calls the user approved during this automation's runs (step agents' approvals,
 * and script gates answered approve), derived like "Approve and remember", deduplicated, in the order they were
 * approved, minus the automation's current grants.
 */
export function proposedGrants(db: Db, automationId: string, tools: (name: string) => Tool | undefined): Grant[] {
  const automation = getAutomation(db, automationId);
  if (!automation) return [];
  const found: Array<{ at: string; grant: Grant }> = [];

  const approved = db
    .select({ tool: approvals.tool, arguments: approvals.arguments, at: approvals.resolved_at })
    .from(approvals)
    .innerJoin(agents, eq(agents.id, approvals.agent_id))
    .innerJoin(automationRuns, eq(automationRuns.id, agents.automation_run_id))
    .where(and(eq(automationRuns.automation_id, automationId), eq(approvals.status, 'approved'), eq(approvals.resolved_by, 'user')))
    .orderBy(asc(approvals.resolved_at))
    .all();
  for (const a of approved) {
    const tool = tools(a.tool);
    if (!tool?.gate) continue;
    let subject: PolicySubject;
    try {
      const raw: unknown = JSON.parse(a.arguments);
      const input = tool.input.safeParse(raw);
      subject = tool.gate.subject(input.success ? input.data : raw, {});
    } catch {
      continue;
    }
    found.push({ at: a.at ?? '', grant: deriveGrant(a.tool, subject, automation.name) });
  }

  // The engine records an approved script gate as {status: 'running', answered_by: 'user', gate} (Task 13).
  const runIds = new Set(
    db
      .select({ id: automationRuns.id })
      .from(automationRuns)
      .where(eq(automationRuns.automation_id, automationId))
      .all()
      .map((r) => r.id),
  );
  const answered = db
    .select()
    .from(events)
    .where(and(eq(events.project_id, automation.project_id), eq(events.type, 'automation.step_changed'), sql`json_extract(${events.payload}, '$.answered_by') = 'user'`))
    .orderBy(asc(events.id))
    .all()
    .map((row) => row as unknown as EventOf<'automation.step_changed'>);
  for (const e of answered) {
    const p = e.payload;
    if (runIds.has(p.run_id) && p.status === 'running' && p.gate) found.push({ at: e.ts, grant: deriveGrant('skill_run', { command: p.gate.subject }, automation.name) });
  }

  // Stable: approvals and gates resolved in the same millisecond keep the order above.
  found.sort((x, y) => (x.at < y.at ? -1 : x.at > y.at ? 1 : 0));
  let out: Grant[] = [];
  for (const { grant } of found) {
    // A grant that does not fit the schema (a very long command) cannot be stored, so it is not proposed.
    if (!Grant.safeParse(grant).success || automation.grants.some((g) => sameGrant(g, grant))) continue;
    out = addGrant(out, grant);
  }
  return out;
}

/** The policy rules of an automation's grants, or none while they are suspended (spec §5.3). */
export function activeGrantRules(a: { grants: Grant[]; grants_suspended: boolean }): PolicyRule[] {
  return a.grants_suspended ? [] : grantRules(a.grants);
}
