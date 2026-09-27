/** A schedule as the Start inspector edits it: a preset, or custom cron. `time` is HH:MM; `day` 0 = Sunday. */
export type SchedulePreset =
  | { kind: 'daily'; time: string }
  | { kind: 'weekdays'; time: string }
  | { kind: 'weekly'; day: number; time: string }
  | { kind: 'monthly'; dom: number; time: string }
  | { kind: 'custom'; cron: string };

/** The computer's timezone: the default for a new schedule. */
export const systemTimezone = (): string => Intl.DateTimeFormat().resolvedOptions().timeZone;

const DAYS = ['Sundays', 'Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays'];
const pad = (n: number) => String(n).padStart(2, '0');
const upTo = (s: string, max: number) => /^\d{1,2}$/.test(s) && Number(s) <= max;

/** The preset a cron expression is, or `custom`. Monthly presets stop at the 28th, which every month has. */
export function presetOf(cron: string): SchedulePreset {
  const f = cron.trim().split(/\s+/);
  if (f.length !== 5) return { kind: 'custom', cron };
  const [m, h, dom, mon, dow] = f as [string, string, string, string, string];
  if (!upTo(m, 59) || !upTo(h, 23) || mon !== '*') return { kind: 'custom', cron };
  const time = `${pad(Number(h))}:${pad(Number(m))}`;
  if (dom === '*' && dow === '*') return { kind: 'daily', time };
  if (dom === '*' && dow === '1-5') return { kind: 'weekdays', time };
  if (dom === '*' && /^[0-6]$/.test(dow)) return { kind: 'weekly', day: Number(dow), time };
  if (upTo(dom, 28) && Number(dom) >= 1 && dow === '*') return { kind: 'monthly', dom: Number(dom), time };
  return { kind: 'custom', cron };
}

export function cronOf(p: SchedulePreset): string {
  if (p.kind === 'custom') return p.cron.trim();
  const [h = 0, m = 0] = p.time.split(':').map(Number);
  switch (p.kind) {
    case 'daily':
      return `${m} ${h} * * *`;
    case 'weekdays':
      return `${m} ${h} * * 1-5`;
    case 'weekly':
      return `${m} ${h} * * ${p.day}`;
    case 'monthly':
      return `${m} ${h} ${p.dom} * *`;
  }
}

const ordinal = (n: number): string => {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  return `${n}${teen ? 'th' : n % 10 === 1 ? 'st' : n % 10 === 2 ? 'nd' : n % 10 === 3 ? 'rd' : 'th'}`;
};

/** "Mondays 08:00", "Weekdays 07:30", "Monthly on the 1st at 09:00", else the cron; plus the timezone when it isn't the computer's. */
export function describeSchedule(cron: string, timezone: string, local = systemTimezone()): string {
  const p = presetOf(cron);
  const text =
    p.kind === 'daily'
      ? `Daily ${p.time}`
      : p.kind === 'weekdays'
        ? `Weekdays ${p.time}`
        : p.kind === 'weekly'
          ? `${DAYS[p.day]} ${p.time}`
          : p.kind === 'monthly'
            ? `Monthly on the ${ordinal(p.dom)} at ${p.time}`
            : `cron ${p.cron}`;
  return timezone === local ? text : `${text} (${timezone})`;
}

/** The list's When column. */
export function whenText(schedules: Array<{ cron: string; timezone: string }>, local = systemTimezone()): string {
  return schedules.length ? schedules.map((s) => describeSchedule(s.cron, s.timezone, local)).join(' · ') : 'Run now only';
}
