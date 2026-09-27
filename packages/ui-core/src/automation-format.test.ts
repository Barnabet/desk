import { describe, expect, it } from 'vitest';
import { automationSummary } from './testing/automations';
import { dayTime, lastRunText, listNote, originText, runStatusText, runTook, skippedText, triggerText, versionBadge } from './automation-format';

describe('automation formats', () => {
  it('badges versions and notes list rows', () => {
    expect(versionBadge({ version: 7, tested_version: 6 })).toBe('v7 · tested in v6');
    expect(versionBadge({ version: 7, tested_version: 7 })).toBe('v7 · tested');
    expect(versionBadge({ version: 2, tested_version: null })).toBe('v2 · untested');
    expect(listNote(automationSummary())).toBe('v7 · untested changes');
    expect(listNote(automationSummary({ version: 2, tested_version: 2, schedules: [], grants_suspended: true }))).toBe('v2 · run now only · grants suspended');
    expect(listNote(automationSummary({ tested_version: 7, enabled: false, enable_requested: true }))).toBe('v7 · Desk proposes turning it on');
  });

  it('words origins, triggers, statuses, last runs and skipped times', () => {
    expect(originText({ origin: 'agent:d1', via: 'tool' })).toBe('Desk');
    expect(originText({ origin: 'user', via: 'editor' })).toBe('You');
    expect(originText({ origin: 'user', via: 'restore' })).toBe('Restored');
    expect(originText({ origin: 'user', via: 'import' })).toBe('Imported');
    expect(triggerText({ trigger: 'test', test: true, version: 6 })).toBe('test · v6');
    expect(triggerText({ trigger: 'manual', test: false, version: 6 })).toBe('Run now');
    expect(runStatusText({ status: 'running', at_step: 'Summarise' })).toEqual({ text: 'running · Summarise', tone: 'run' });
    expect(runStatusText({ status: 'failed', at_step: 'Fetch pages' })).toEqual({ text: 'failed at Fetch pages', tone: 'fail' });
    expect(runStatusText({ status: 'waiting', at_step: null })).toEqual({ text: 'waiting on you', tone: 'wait' });
    expect(runStatusText({ status: 'cancelled', at_step: null })).toEqual({ text: 'cancelled', tone: 'idle' });
    const last = { id: 'r', number: 2, trigger: 'schedule' as const, test: false, started_at: '2026-09-28T06:00:00.000Z', summary: null };
    const now = Date.parse('2026-09-28T09:00:00.000Z');
    expect(lastRunText({ ...last, status: 'waiting', finished_at: null, waiting_on: 'Ask me: Publish?' }, now)).toEqual({ text: 'waiting on you: Ask me: Publish?', tone: 'wait' });
    expect(lastRunText({ ...last, status: 'succeeded', finished_at: '2026-09-28T07:00:00.000Z', waiting_on: null }, now)).toEqual({ text: 'succeeded · 2h ago', tone: 'done' });
    expect(lastRunText({ ...last, status: 'running', finished_at: null, waiting_on: null }, now)).toEqual({ text: 'running', tone: 'run' });
    expect(skippedText({ kind: 'skipped', automation_id: 'a1', trigger_index: 0, due_at: 't', reason: 'still_running', ts: 't' })).toBe('skipped: the previous run was still going');
  });

  it('says local days relative to now, and durations', () => {
    const now = new Date(2026, 8, 28, 9, 30).getTime(); // Monday 28 September 2026
    expect(dayTime(new Date(2026, 8, 28, 8, 0).toISOString(), now)).toBe('today 08:00');
    expect(dayTime(new Date(2026, 8, 29, 2, 0).toISOString(), now)).toBe('tomorrow 02:00');
    expect(dayTime(new Date(2026, 8, 27, 2, 0).toISOString(), now)).toBe('yesterday 02:00');
    expect(dayTime(new Date(2026, 9, 2, 8, 0).toISOString(), now)).toBe('Fri 08:00');
    expect(dayTime(new Date(2026, 9, 12, 8, 5).toISOString(), now)).toBe('Oct 12 08:05');
    expect(runTook({ started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:09:00.000Z' }, 0)).toBe('9m');
    expect(runTook({ started_at: '2026-09-28T06:00:00.000Z', finished_at: null }, Date.parse('2026-09-28T06:00:40.000Z'))).toBe('40s');
  });
});
