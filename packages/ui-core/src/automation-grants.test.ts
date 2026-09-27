import { describe, expect, it } from 'vitest';
import type { Grant } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { describeGrant, grantKey, grantOrigins, sameGrant, uniqueGrants, widenDomain } from './automation-grants';

describe('grants', () => {
  it('compares, dedupes and describes grants', () => {
    const a: Grant = { tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' };
    expect(sameGrant(a, { tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' })).toBe(true);
    expect(sameGrant({ tool: 'bash', action: 'allow' }, { tool: 'bash', match: {}, action: 'allow' })).toBe(true);
    expect(uniqueGrants([a, { ...a }, { tool: 'bash', action: 'deny' }])).toHaveLength(2);
    expect(describeGrant(a)).toBe('Allow web_fetch on example.com');
    expect(describeGrant({ tool: 'bash', action: 'deny' })).toBe('Deny any bash call');
    expect(describeGrant({ tool: 'git_push', match: { branch: 'desk/auto-digest-*' }, action: 'allow' })).toBe('Allow git_push on desk/auto-digest-*');
  });

  it('widens domains like the daemon', () => {
    expect(widenDomain('news.bbc.co.uk')).toEqual(['bbc.co.uk', '*.bbc.co.uk']);
    expect(widenDomain('api.example.com')).toEqual(['example.com', '*.example.com']);
    expect(widenDomain('10.0.0.1')).toEqual(['10.0.0.1']);
    expect(widenDomain('localhost')).toEqual(['localhost']);
  });

  it('traces each grant to the change that added it', () => {
    const fetch: Grant = { tool: 'web_fetch', match: { domain: 'a.com' }, action: 'allow' };
    const bash: Grant = { tool: 'bash', match: { command: '^ls$' }, action: 'allow' };
    const events = [
      ev(1, 'automation.grants_set', { automation_id: 'a1', grants: [fetch], reason: 'enabled' }),
      ev(2, 'automation.grants_set', { automation_id: 'a1', grants: [fetch, bash], reason: 'remembered', source: { run_id: 'r1', step_id: 'sum' } }),
      ev(3, 'automation.grants_set', { automation_id: 'other', grants: [], reason: 'edited' }),
      ev(4, 'automation.grants_set', { automation_id: 'a1', grants: [fetch, bash], reason: 'kept' }),
    ];
    const o = grantOrigins(events, 'a1');
    expect(o.get(grantKey(fetch))).toMatchObject({ kind: 'enabled' });
    expect(o.get(grantKey(bash))).toMatchObject({ kind: 'remembered', run_id: 'r1', step_id: 'sum' });
    const later = grantOrigins([...events, ev(5, 'automation.grants_set', { automation_id: 'a1', grants: [bash], reason: 'edited' })], 'a1');
    expect(later.has(grantKey(fetch))).toBe(false);
  });
});
