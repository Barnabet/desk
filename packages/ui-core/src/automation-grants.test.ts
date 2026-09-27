import { describe, expect, it } from 'vitest';
import type { Grant } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { dayTime } from './automation-format';
import { describeGrant, grantDraft, grantFromDraft, grantKey, grantOrigins, grantOriginText, replaceGrant, sameGrant, uniqueGrants, widenDomain, widenedGrants } from './automation-grants';
import { digestDef } from './testing/automations';

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

describe('editing grants', () => {
  it('turns grants into drafts and drafts back into grants, or says what is wrong', () => {
    expect(grantDraft()).toEqual({ tool: '', action: 'allow', kind: 'any', value: '' });
    expect(grantDraft({ tool: 'bash', match: { command: '^ls$' }, action: 'deny' })).toEqual({ tool: 'bash', action: 'deny', kind: 'command', value: '^ls$' });
    expect(grantFromDraft({ tool: ' bash ', action: 'allow', kind: 'any', value: 'ignored' })).toEqual({ grant: { tool: 'bash', action: 'allow' } });
    expect(grantFromDraft({ tool: 'web_fetch', action: 'allow', kind: 'domain', value: ' News.BBC.co.uk ' })).toEqual({ grant: { tool: 'web_fetch', action: 'allow', match: { domain: 'news.bbc.co.uk' } } });
    expect(grantFromDraft({ tool: '', action: 'allow', kind: 'any', value: '' })).toEqual({ problem: 'Name a tool.' });
    expect(grantFromDraft({ tool: 'bash', action: 'allow', kind: 'command', value: '' })).toEqual({ problem: 'Say which command.' });
    expect(grantFromDraft({ tool: 'bash', action: 'allow', kind: 'command', value: '(' })).toEqual({ problem: 'Not a valid regular expression.' });
    expect(grantFromDraft({ tool: 'git_push', action: 'allow', kind: 'branch', value: 'x'.repeat(201) })).toEqual({ problem: 'At most 200 characters.' });
  });

  it('widens a web grant on an exact host, and replaces grants in place', () => {
    const fetch: Grant = { tool: 'web_fetch', match: { domain: 'news.bbc.co.uk' }, action: 'allow' };
    expect(widenedGrants(fetch)).toEqual([
      { tool: 'web_fetch', match: { domain: 'bbc.co.uk' }, action: 'allow' },
      { tool: 'web_fetch', match: { domain: '*.bbc.co.uk' }, action: 'allow' },
    ]);
    expect(widenedGrants({ ...fetch, match: { domain: '*.bbc.co.uk' } })).toBeNull();
    expect(widenedGrants({ ...fetch, match: { domain: 'localhost' } })).toBeNull();
    expect(widenedGrants({ tool: 'bash', match: { domain: 'a.com' }, action: 'allow' })).toBeNull();
    const bash: Grant = { tool: 'bash', action: 'allow' };
    expect(replaceGrant([fetch, bash], 0, widenedGrants(fetch)!)).toHaveLength(3);
    expect(replaceGrant([fetch, bash], 1, [])).toEqual([fetch]);
    expect(replaceGrant([fetch, bash], 2, [fetch])).toEqual([fetch, bash]);
  });

  it('words where a grant came from', () => {
    const now = Date.parse('2026-09-28T09:00:00.000Z');
    const ts = '2026-09-28T07:00:00.000Z';
    expect(grantOriginText({ kind: 'remembered', run_id: 'r1', step_id: 'sum', ts }, digestDef(), now)).toBe(`Remembered at Summarise · ${dayTime(ts, now)}`);
    expect(grantOriginText({ kind: 'remembered', run_id: 'r1', step_id: 'gone', ts }, digestDef(), now)).toBe(`Remembered at gone · ${dayTime(ts, now)}`);
    expect(grantOriginText({ kind: 'enabled', ts }, digestDef(), now)).toBe(`Set when you turned it on · ${dayTime(ts, now)}`);
    expect(grantOriginText({ kind: 'edited', ts }, digestDef(), now)).toBe(`Added by you · ${dayTime(ts, now)}`);
    expect(grantOriginText(undefined, digestDef(), now)).toBeNull();
  });
});
