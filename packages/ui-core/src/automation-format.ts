import type { AutomationSummary, RunInfo, RunListEntry, StepKind } from '@desk/protocol';
import { duration, since } from './format';

/** Step kinds as the editor names them. */
export const STEP_KIND_LABEL: Record<StepKind, string> = { script: 'Script', agent: 'Agent', ask: 'Ask me', wait: 'Wait', automation: 'Run automation', tell_desk: 'Tell Desk' };

/** "v7 · tested", "v7 · tested in v6" or "v7 · untested". */
export function versionBadge(a: Pick<AutomationSummary, 'version' | 'tested_version'>): string {
  if (a.tested_version === a.version) return `v${a.version} · tested`;
  return a.tested_version ? `v${a.version} · tested in v${a.tested_version}` : `v${a.version} · untested`;
}

/** The note under a title in the list: "v7 · untested changes · run now only". */
export function listNote(a: Pick<AutomationSummary, 'version' | 'tested_version' | 'schedules' | 'grants_suspended' | 'enable_requested' | 'enabled'>): string {
  const parts = [`v${a.version}`];
  if (a.tested_version !== a.version) parts.push(a.tested_version ? 'untested changes' : 'untested');
  if (!a.schedules.length) parts.push('run now only');
  if (a.grants_suspended) parts.push('grants suspended');
  if (a.enable_requested && !a.enabled) parts.push('Desk proposes turning it on');
  return parts.join(' · ');
}

/** Who saved a version. */
export function originText(v: { origin: string; via: string }): string {
  if (v.via === 'restore') return 'Restored';
  if (v.via === 'import') return 'Imported';
  if (v.origin.startsWith('agent:')) return 'Desk';
  return v.via === 'cli' ? 'You (CLI)' : 'You';
}

const TRIGGER: Record<RunInfo['trigger'], string> = { schedule: 'schedule', manual: 'Run now', desk: 'Desk', parent: 'another automation', test: 'test' };

/** The Trigger column: "schedule", "Run now", "test · v6"… */
export function triggerText(run: Pick<RunInfo, 'trigger' | 'test' | 'version'>): string {
  return run.test ? `test · v${run.version}` : TRIGGER[run.trigger];
}

export type RunTone = 'run' | 'wait' | 'done' | 'fail' | 'idle';

/** The Status column: "running · Summarise", "waiting on you · Publish?", "failed at Fetch pages". */
export function runStatusText(run: Pick<RunInfo, 'status' | 'at_step'>): { text: string; tone: RunTone } {
  switch (run.status) {
    case 'running':
      return { text: run.at_step ? `running · ${run.at_step}` : 'running', tone: 'run' };
    case 'waiting':
      return { text: run.at_step ? `waiting on you · ${run.at_step}` : 'waiting on you', tone: 'wait' };
    case 'succeeded':
      return { text: 'succeeded', tone: 'done' };
    case 'failed':
      return { text: run.at_step ? `failed at ${run.at_step}` : 'failed', tone: 'fail' };
    default:
      return { text: 'cancelled', tone: 'idle' };
  }
}

/** The list's Last run column: "waiting on you: Ask me: Publish?", "failed · 3d ago", "succeeded · 2h ago", "running". */
export function lastRunText(r: NonNullable<AutomationSummary['last_run']>, now: number): { text: string; tone: RunTone } {
  if (r.status === 'waiting') return { text: r.waiting_on ? `waiting on you: ${r.waiting_on}` : 'waiting on you', tone: 'wait' };
  const s = runStatusText({ status: r.status, at_step: null });
  return r.finished_at ? { ...s, text: `${s.text} · ${since(r.finished_at, now)}` } : s;
}

/** Why a schedule time started nothing. */
export function skippedText(e: Extract<RunListEntry, { kind: 'skipped' }>): string {
  return e.reason === 'still_running' ? 'skipped: the previous run was still going' : 'skipped: missed while Desk was off';
}

/** How long a run took, or has been going. */
export const runTook = (run: Pick<RunInfo, 'started_at' | 'finished_at'>, now: number): string => duration((run.finished_at ? Date.parse(run.finished_at) : now) - Date.parse(run.started_at));

const pad = (n: number) => String(n).padStart(2, '0');
const DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** A local day and time: "today 08:00", "tomorrow 02:00", "Mon 08:00" within a week either way, else "Oct 12 08:00". */
export function dayTime(ts: string, now: number): string {
  const d = new Date(ts);
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(d) - midnight(new Date(now))) / 86_400_000);
  if (days === 0) return `today ${time}`;
  if (days === 1) return `tomorrow ${time}`;
  if (days === -1) return `yesterday ${time}`;
  if (Math.abs(days) < 7) return `${DAY[d.getDay()]} ${time}`;
  return `${MONTH[d.getMonth()]} ${d.getDate()} ${time}`;
}
