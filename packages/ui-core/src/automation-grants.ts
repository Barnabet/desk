import type { AutomationDefinition, Grant, StoredEvent } from '@desk/protocol';
import { dayTime } from './automation-format';

/** One identity per grant: tool, action and match (a missing field and an empty match are the same). */
export const grantKey = (g: Grant): string => JSON.stringify([g.tool, g.action, g.match?.domain ?? null, g.match?.command ?? null, g.match?.branch ?? null]);

export const sameGrant = (a: Grant, b: Grant): boolean => grantKey(a) === grantKey(b);

/** Grants in order, each once. */
export function uniqueGrants(list: Grant[]): Grant[] {
  const seen = new Set<string>();
  return list.filter((g) => {
    const k = grantKey(g);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/** "Allow web_fetch on example.com", "Allow skill_run matching ^digest/fetch\.py(\s|$)", "Deny any bash call". */
export function describeGrant(g: Grant): string {
  const m = g.match;
  const what = m?.domain ? `${g.tool} on ${m.domain}` : m?.command ? `${g.tool} matching ${m.command}` : m?.branch ? `${g.tool} on ${m.branch}` : `any ${g.tool} call`;
  return `${g.action === 'allow' ? 'Allow' : 'Deny'} ${what}`;
}

const SECOND_LEVEL = new Set(['co', 'com', 'net', 'org', 'gov', 'ac', 'edu']);

/**
 * The widening the Grants tab offers for a domain (the same rule as core's `widenDomain`): `news.bbc.co.uk` →
 * `['bbc.co.uk', '*.bbc.co.uk']`. IP addresses, single labels and bare public suffixes stay as they are.
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

export type GrantOrigin = { kind: 'remembered'; run_id: string; step_id: string; ts: string } | { kind: 'edited' | 'enabled'; ts: string };

/** Where each current grant came from: the `automation.grants_set` that added it. Later sets keep the origins of grants they keep. */
export function grantOrigins(events: readonly StoredEvent[], automationId: string): Map<string, GrantOrigin> {
  const origins = new Map<string, GrantOrigin>();
  for (const e of events) {
    if (e.type !== 'automation.grants_set' || e.payload.automation_id !== automationId) continue;
    const p = e.payload;
    const now = new Set(p.grants.map(grantKey));
    for (const k of [...origins.keys()]) if (!now.has(k)) origins.delete(k);
    for (const k of now) {
      if (origins.has(k)) continue;
      origins.set(k, p.reason === 'remembered' && p.source ? { kind: 'remembered', run_id: p.source.run_id, step_id: p.source.step_id, ts: e.ts } : { kind: p.reason === 'enabled' ? 'enabled' : 'edited', ts: e.ts });
    }
  }
  return origins;
}

/** The tools a grant is usually for (the Grants tab's suggestions). Any tool name is accepted. */
export const GRANT_TOOLS = ['web_fetch', 'web_search', 'skill_run', 'bash', 'bash_background', 'git_push', 'open_pr'];

/** What a grant matches: every call of its tool, or one field of its `match`. */
export type GrantMatchKind = 'any' | 'domain' | 'command' | 'branch';

export const GRANT_MATCH_LABEL: Record<GrantMatchKind, string> = { any: 'Any call', domain: 'A domain', command: 'A command', branch: 'A branch' };

/** The value field's label for each kind of match (the policy's domain globs, command regexes and branch globs). */
export const GRANT_VALUE_LABEL: Record<Exclude<GrantMatchKind, 'any'>, string> = {
  domain: 'Domain (a host, or *.example.com)',
  command: 'Command (a regular expression)',
  branch: 'Branch (a glob)',
};

/** A grant while the user edits it. */
export type GrantDraft = { tool: string; action: Grant['action']; kind: GrantMatchKind; value: string };

export function grantDraft(g?: Grant): GrantDraft {
  if (!g) return { tool: '', action: 'allow', kind: 'any', value: '' };
  const m = g.match;
  const kind: GrantMatchKind = m?.domain ? 'domain' : m?.command ? 'command' : m?.branch ? 'branch' : 'any';
  return { tool: g.tool, action: g.action, kind, value: m?.domain ?? m?.command ?? m?.branch ?? '' };
}

/** The protocol's limits on each match field (`Grant` in `protocol/src/automations.ts`). */
const MATCH_MAX: Record<Exclude<GrantMatchKind, 'any'>, number> = { domain: 253, command: 1000, branch: 200 };

/** The grant a draft describes, or what is wrong with it: the protocol's limits, and a command must compile as a regex. */
export function grantFromDraft(d: GrantDraft): { grant: Grant } | { problem: string } {
  const tool = d.tool.trim();
  if (!tool) return { problem: 'Name a tool.' };
  if (tool.length > 60) return { problem: 'A tool name is at most 60 characters.' };
  if (d.kind === 'any') return { grant: { tool, action: d.action } };
  const raw = d.value.trim();
  const value = d.kind === 'domain' ? raw.toLowerCase() : raw;
  if (!value) return { problem: `Say which ${d.kind}.` };
  if (value.length > MATCH_MAX[d.kind]) return { problem: `At most ${MATCH_MAX[d.kind]} characters.` };
  if (d.kind === 'command') {
    try {
      new RegExp(value);
    } catch {
      return { problem: 'Not a valid regular expression.' };
    }
  }
  return { grant: { tool, action: d.action, match: { [d.kind]: value } } };
}

/**
 * A web grant on an exact host widened (spec §5.3): its registrable domain and every subdomain, two grants because
 * `*.x` does not match `x`. Null for other tools, wildcards and hosts that do not widen.
 */
export function widenedGrants(g: Grant): Grant[] | null {
  const host = g.match?.domain;
  if (!host || host.startsWith('*.') || (g.tool !== 'web_fetch' && g.tool !== 'web_search')) return null;
  const wide = widenDomain(host);
  return wide.length < 2 ? null : wide.map((domain) => ({ ...g, match: { domain } }));
}

/** `list` with the grant at `i` replaced by `next`: none removes it, `i === list.length` appends. Each grant stays once. */
export function replaceGrant(list: Grant[], i: number, next: Grant[]): Grant[] {
  return uniqueGrants([...list.slice(0, i), ...next, ...list.slice(i + 1)]);
}

/** Where a grant came from (spec §8.4), or null before the project's events have loaded. */
export function grantOriginText(origin: GrantOrigin | undefined, def: AutomationDefinition, now: number): string | null {
  if (!origin) return null;
  const when = dayTime(origin.ts, now);
  if (origin.kind === 'remembered') return `Remembered at ${def.steps.find((s) => s.id === origin.step_id)?.title ?? origin.step_id} · ${when}`;
  return `${origin.kind === 'enabled' ? 'Set when you turned it on' : 'Added by you'} · ${when}`;
}
