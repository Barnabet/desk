import { describe, expect, it } from 'vitest';
import { cronOf, describeSchedule, presetOf, whenText, type SchedulePreset } from './automation-schedules';

describe('schedules', () => {
  it('recognises presets and builds their cron', () => {
    const cases: Array<[string, SchedulePreset]> = [
      ['0 8 * * *', { kind: 'daily', time: '08:00' }],
      ['30 7 * * 1-5', { kind: 'weekdays', time: '07:30' }],
      ['0 8 * * 1', { kind: 'weekly', day: 1, time: '08:00' }],
      ['5 9 1 * *', { kind: 'monthly', dom: 1, time: '09:05' }],
    ];
    for (const [cron, preset] of cases) {
      expect(presetOf(cron)).toEqual(preset);
      expect(cronOf(preset)).toBe(cron);
    }
    expect(presetOf('*/15 * * * *')).toEqual({ kind: 'custom', cron: '*/15 * * * *' });
    expect(presetOf('0 8 31 * *')).toEqual({ kind: 'custom', cron: '0 8 31 * *' });
    expect(cronOf({ kind: 'custom', cron: ' 0 8 * * 1 ' })).toBe('0 8 * * 1');
  });

  it('describes schedules, naming the timezone only when it is not the Mac’s', () => {
    expect(describeSchedule('0 8 * * 1', 'Europe/Paris', 'Europe/Paris')).toBe('Mondays 08:00');
    expect(describeSchedule('0 2 * * *', 'America/New_York', 'Europe/Paris')).toBe('Daily 02:00 (America/New_York)');
    expect(describeSchedule('0 9 2 * *', 'UTC', 'UTC')).toBe('Monthly on the 2nd at 09:00');
    expect(whenText([], 'UTC')).toBe('Run now only');
    expect(whenText([{ cron: '0 8 * * 1-5', timezone: 'UTC' }, { cron: '*/30 * * * *', timezone: 'UTC' }], 'UTC')).toBe('Weekdays 08:00 · cron */30 * * * *');
  });
});
