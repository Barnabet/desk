import { Cron } from 'croner';
import { MIN_SCHEDULE_GAP_MS } from '@desk/protocol';

// Schedules (spec 2026-09-26-automations-design §5.1): 5-field cron in an IANA timezone, computed with croner.
// croner's timezone conversions cost ~0.2 ms per computed time, so nothing here walks an unbounded range.

/** Whether `timezone` is an IANA name (or an offset such as +01:00) this runtime can convert to. */
export function isTimezone(timezone: string): boolean {
  if (!timezone.trim()) return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: timezone });
    return true;
  } catch {
    return false;
  }
}

/** A paused job: only computes times, never fires. Throws on a malformed pattern. */
function job(cron: string, timezone: string): Cron {
  return new Cron(cron.trim(), { timezone, paused: true, mode: '5-part' });
}

function minutesText(ms: number): string {
  const m = Math.round(ms / 60_000);
  return m === 1 ? 'minute' : `${m} minutes`;
}

/** Why a schedule cannot be saved, or null. Checks the field count, the timezone, the pattern, a future run, and the frequency limit on the next 10 runs. */
export function checkSchedule(cron: string, timezone: string, now: Date = new Date()): string | null {
  const fields = cron.trim().split(/\s+/).filter(Boolean);
  if (fields.length !== 5) return `Use 5 fields (minute hour day-of-month month day-of-week): '${cron}' has ${fields.length}`;
  if (!isTimezone(timezone)) return `Unknown timezone '${timezone}': use an IANA name such as Europe/Paris`;
  let runs: Date[];
  try {
    runs = job(cron, timezone).nextRuns(10, now);
  } catch (e) {
    return `Invalid schedule '${cron}': ${e instanceof Error ? e.message : String(e)}`;
  }
  if (!runs.length) return `'${cron}' never runs`;
  let gap = Infinity;
  for (let i = 1; i < runs.length; i++) gap = Math.min(gap, runs[i]!.getTime() - runs[i - 1]!.getTime());
  if (gap < MIN_SCHEDULE_GAP_MS) return `Runs at most every ${minutesText(MIN_SCHEDULE_GAP_MS)}: '${cron}' runs every ${minutesText(gap)}`;
  return null;
}

/** The first time strictly after `after`, or null when there is none. */
export function nextDue(cron: string, timezone: string, after: Date): Date | null {
  return job(cron, timezone).nextRun(after);
}

/** The next `n` times strictly after `from` (the editor's preview, `next_times`). */
export function nextTimes(cron: string, timezone: string, n: number, from: Date): Date[] {
  return job(cron, timezone).nextRuns(n, from);
}

/** Times in (after, until], ascending, at most `limit` of them from `from` on. */
function walk(j: Cron, from: Date, until: Date, limit: number): Date[] {
  const out: Date[] = [];
  let cur = from;
  while (out.length < limit) {
    const next = j.nextRun(cur);
    if (!next || next.getTime() > until.getTime()) break;
    out.push(next);
    cur = next;
  }
  return out;
}

/**
 * The due times in (after, until], ascending. When there are more than `cap`, only the latest `cap` are returned, so
 * the last element is always the latest due time and `length - 1` counts the skipped ones (at most `cap - 1`).
 * Long gaps (a Mac asleep for weeks) are not walked from the start: the tail is found from a window before `until`.
 */
export function dueTimes(cron: string, timezone: string, after: Date, until: Date, cap = 1000): Date[] {
  if (cap < 1 || until.getTime() <= after.getTime()) return [];
  const j = job(cron, timezone);
  const head = walk(j, after, until, cap + 1);
  if (head.length <= cap) return head;
  // More than `cap`: `span` is roughly how long `cap` occurrences take. Widen the window until it holds `cap` of them.
  let span = head[cap - 1]!.getTime() - after.getTime();
  for (;;) {
    const start = new Date(Math.max(after.getTime(), until.getTime() - span));
    const tail: Date[] = [];
    let cur = start;
    for (;;) {
      const next = j.nextRun(cur);
      if (!next || next.getTime() > until.getTime()) break;
      tail.push(next);
      if (tail.length > cap) tail.shift();
      cur = next;
    }
    if (tail.length >= cap || start.getTime() === after.getTime()) return tail;
    span *= 2;
  }
}

function localParts(timezone: string, at: Date): { year: string; month: string; day: string; hour: string; minute: string } {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  });
  const parts = fmt.formatToParts(at);
  const get = (type: Intl.DateTimeFormatPartTypes): string => parts.find((p) => p.type === type)?.value ?? '00';
  return { year: get('year'), month: get('month'), day: get('day'), hour: get('hour'), minute: get('minute') };
}

/** `YYYY-MM-DD` in `timezone` (`{{run.date}}`). */
export function localDate(timezone: string, at: Date): string {
  const p = localParts(timezone, at);
  return `${p.year}-${p.month}-${p.day}`;
}

/** `YYYY-MM-DD HHmm` in `timezone`: the library folder of a run's published files. */
export function localStamp(timezone: string, at: Date): string {
  const p = localParts(timezone, at);
  return `${p.year}-${p.month}-${p.day} ${p.hour}${p.minute}`;
}

/** The next HH:MM in `timezone` strictly after `from` (a Wait step's `until`). */
export function nextClock(until: string, timezone: string, from: Date): Date {
  const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(until);
  if (!m) throw new Error(`Use HH:MM (24 hours), not '${until}'`);
  const next = job(`${Number(m[2])} ${Number(m[1])} * * *`, timezone).nextRun(from);
  if (!next) throw new Error(`No next ${until} in ${timezone}`);
  return next;
}

/** The daemon's timezone: the default for Wait and `{{run.date}}` when an automation has no schedule. */
export function systemTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}
