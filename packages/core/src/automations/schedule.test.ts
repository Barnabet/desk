import { describe, expect, it } from 'vitest';
import { checkSchedule, dueTimes, isTimezone, localDate, localStamp, nextClock, nextDue, nextTimes, systemTimezone } from './schedule';

const at = (iso: string) => new Date(iso);
const iso = (ds: Date[]) => ds.map((d) => d.toISOString());
const NOW = at('2026-09-26T10:00:00.000Z');

describe('schedules', () => {
  it('follows Monday 08:00 in Paris across the October DST change', () => {
    expect(nextDue('0 8 * * 1', 'Europe/Paris', at('2026-10-13T00:00:00.000Z'))!.toISOString()).toBe('2026-10-19T06:00:00.000Z'); // CEST, UTC+2
    expect(nextDue('0 8 * * 1', 'Europe/Paris', at('2026-10-20T00:00:00.000Z'))!.toISOString()).toBe('2026-10-26T07:00:00.000Z'); // CET, UTC+1
    // Strictly after: a due time itself is not due again.
    expect(nextDue('0 8 * * 1', 'Europe/Paris', at('2026-10-19T06:00:00.000Z'))!.toISOString()).toBe('2026-10-26T07:00:00.000Z');
  });

  it('allows every 5 minutes and refuses more often', () => {
    expect(checkSchedule('*/5 * * * *', 'UTC', NOW)).toBeNull();
    expect(checkSchedule('0 8 * * 1-5', 'Europe/Paris', NOW)).toBeNull();
    expect(checkSchedule('*/2 * * * *', 'UTC', NOW)).toBe("Runs at most every 5 minutes: '*/2 * * * *' runs every 2 minutes");
    expect(checkSchedule('* * * * *', 'UTC', NOW)).toBe("Runs at most every 5 minutes: '* * * * *' runs every minute");
    // Irregular patterns are checked on their closest pair among the next 10 runs.
    expect(checkSchedule('0,3 9 * * *', 'UTC', NOW)).toBe("Runs at most every 5 minutes: '0,3 9 * * *' runs every 3 minutes");
  });

  it('needs exactly 5 fields', () => {
    expect(checkSchedule('0 8 * *', 'UTC', NOW)).toBe("Use 5 fields (minute hour day-of-month month day-of-week): '0 8 * *' has 4");
    expect(checkSchedule('0 0 8 * * *', 'UTC', NOW)).toBe("Use 5 fields (minute hour day-of-month month day-of-week): '0 0 8 * * *' has 6");
    expect(checkSchedule('@daily', 'UTC', NOW)).toMatch(/has 1$/);
  });

  it('refuses a bad timezone, a bad pattern and a schedule that never runs', () => {
    expect(isTimezone('Europe/Paris')).toBe(true);
    expect(isTimezone('Mars/Olympus')).toBe(false);
    expect(isTimezone('')).toBe(false);
    expect(checkSchedule('0 8 * * *', 'Mars/Olympus', NOW)).toBe("Unknown timezone 'Mars/Olympus': use an IANA name such as Europe/Paris");
    expect(checkSchedule('61 8 * * *', 'UTC', NOW)).toMatch(/^Invalid schedule '61 8 \* \* \*': .*minute/);
    expect(checkSchedule('0 0 31 2 *', 'UTC', NOW)).toBe("'0 0 31 2 *' never runs");
  });

  it('handles 29 February', () => {
    expect(checkSchedule('0 9 29 2 *', 'UTC', NOW)).toBeNull();
    expect(iso(nextTimes('0 9 29 2 *', 'UTC', 2, NOW))).toEqual(['2028-02-29T09:00:00.000Z', '2032-02-29T09:00:00.000Z']);
  });

  it('counts the due times of missed weeks, across the DST change', () => {
    const after = at('2026-09-28T06:00:00.000Z'); // the Monday it last fired
    expect(iso(dueTimes('0 8 * * 1', 'Europe/Paris', after, at('2026-10-20T12:00:00.000Z')))).toEqual([
      '2026-10-05T06:00:00.000Z',
      '2026-10-12T06:00:00.000Z',
      '2026-10-19T06:00:00.000Z',
    ]);
    expect(iso(dueTimes('0 8 * * 1', 'Europe/Paris', after, at('2026-11-02T07:00:00.000Z'))).slice(-2)).toEqual(['2026-10-26T07:00:00.000Z', '2026-11-02T07:00:00.000Z']); // until is inclusive
    expect(dueTimes('0 8 * * 1', 'Europe/Paris', after, at('2026-10-05T05:59:00.000Z'))).toEqual([]);
    expect(dueTimes('0 8 * * 1', 'Europe/Paris', after, after)).toEqual([]);
  });

  it('keeps the latest due times when there are more than the cap', () => {
    const after = at('2026-09-01T00:00:00.000Z');
    const until = at('2026-09-08T00:02:00.000Z'); // a week of */5: 2016 due times
    const tail = dueTimes('*/5 * * * *', 'UTC', after, until, 10);
    expect(tail).toHaveLength(10);
    expect(tail.at(-1)!.toISOString()).toBe('2026-09-08T00:00:00.000Z');
    expect(tail[0]!.toISOString()).toBe('2026-09-07T23:15:00.000Z');
    expect(iso(dueTimes('0 8 * * *', 'UTC', after, at('2026-09-04T09:00:00.000Z'), 2))).toEqual(['2026-09-03T08:00:00.000Z', '2026-09-04T08:00:00.000Z']);
  });

  it('finds the next clock time, rolling to the next day', () => {
    expect(nextClock('08:00', 'Europe/Paris', at('2026-09-26T05:00:00.000Z')).toISOString()).toBe('2026-09-26T06:00:00.000Z'); // 07:00 in Paris
    expect(nextClock('08:00', 'Europe/Paris', at('2026-09-26T07:30:00.000Z')).toISOString()).toBe('2026-09-27T06:00:00.000Z'); // 09:30 in Paris
    expect(nextClock('23:45', 'America/New_York', at('2026-09-26T12:00:00.000Z')).toISOString()).toBe('2026-09-27T03:45:00.000Z');
    expect(() => nextClock('24:00', 'UTC', NOW)).toThrow('HH:MM');
  });

  it('formats local dates and stamps near midnight', () => {
    const t = at('2026-09-26T22:30:00.000Z');
    expect(localDate('Europe/Paris', t)).toBe('2026-09-27'); // 00:30 CEST
    expect(localDate('America/New_York', t)).toBe('2026-09-26'); // 18:30 EDT
    expect(localStamp('Europe/Paris', t)).toBe('2026-09-27 0030');
    expect(localStamp('America/New_York', t)).toBe('2026-09-26 1830');
    expect(localStamp('UTC', at('2026-09-26T00:05:00.000Z'))).toBe('2026-09-26 0005');
    expect(isTimezone(systemTimezone())).toBe(true);
  });
});
