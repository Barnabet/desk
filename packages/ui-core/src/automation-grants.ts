import type { Grant, StoredEvent } from '@desk/protocol';

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
