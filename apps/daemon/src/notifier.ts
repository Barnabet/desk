import { execFile } from 'node:child_process';
import { WAKES_PAUSED, type StoredEvent } from '@desk/protocol';
import { getAgent, getAutomation, getProject, getRun, getVersion, stepAgentOf, type Db, type EventStore } from '@desk/core';

export type Notification = { title: string; body: string };

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** What (if anything) to tell the user about an event when no desktop client is showing notifications. */
export function notificationFor(ev: StoredEvent, db: Db): Notification | null {
  const project = getProject(db, ev.project_id);
  if (!project) return null;
  const title = `Desk · ${project.name}`;
  const name = (id: string | null) => {
    const a = id ? getAgent(db, id) : undefined;
    if (a?.role === 'step') {
      const link = stepAgentOf(db, a.id);
      const def = link ? getVersion(db, link.run.automation_id, link.run.version)?.definition : undefined;
      return `${def?.title ?? 'An automation'} · ${a.title ?? 'a step'}`;
    }
    return a?.role === 'desk' ? 'Desk' : (a?.title ?? 'A thread');
  };
  /** The run's automation title, for a top-level run only (a child's parent reports). */
  const runTitle = (runId: string, opts: { topLevel: boolean }) => {
    const run = getRun(db, runId);
    if (!run || (opts.topLevel && (run.parent_run_id || run.test))) return null;
    return { run, def: getVersion(db, run.automation_id, run.version)?.definition };
  };
  switch (ev.type) {
    case 'approval.requested':
      return ev.payload.delegate_to_desk ? null : { title, body: `${name(ev.agent_id)} wants to run ${ev.payload.tool}` };
    case 'question.asked':
      return { title, body: truncate(`Desk asks: ${ev.payload.question}`, 200) };
    case 'report':
      return ev.payload.needs_you.length ? { title, body: truncate(`${ev.payload.headline} (${ev.payload.needs_you.length} need you)`, 200) } : null;
    case 'message.agent':
      return ev.payload.kind === 'stalled' ? { title, body: `${name(ev.payload.from_agent_id)} has stalled` } : null;
    case 'agent.status_changed': {
      if (ev.payload.status !== 'failed') return null;
      const a = ev.agent_id ? getAgent(db, ev.agent_id) : undefined;
      if (a?.role !== 'thread') return null;
      return { title, body: truncate(`${a.title ?? 'A thread'} failed${ev.payload.reason ? `: ${ev.payload.reason}` : ''}`, 200) };
    }
    case 'automation.step_changed': {
      if (ev.payload.status !== 'waiting' || (!ev.payload.question && !ev.payload.gate)) return null;
      const r = runTitle(ev.payload.run_id, { topLevel: false });
      const t = r?.def?.title ?? 'An automation';
      return { title, body: truncate(ev.payload.question ? `${t}: ${ev.payload.question.text}` : `${t} wants to run ${ev.payload.gate!.subject}`, 200) };
    }
    case 'automation.run_finished': {
      const r = runTitle(ev.payload.run_id, { topLevel: true });
      if (!r) return null;
      const t = r.def?.title ?? 'An automation';
      if (ev.payload.status === 'failed') return { title, body: truncate(`${t} failed: ${ev.payload.reason ?? ev.payload.summary}`, 200) };
      if (ev.payload.status === 'succeeded' && r.def?.after_run === 'notify') return { title, body: truncate(`${t}: ${ev.payload.summary}`, 200) };
      return null;
    }
    case 'automation.enable_requested': {
      const a = getAutomation(db, ev.payload.automation_id);
      return a ? { title, body: truncate(`Desk proposes turning on ${a.title}`, 200) } : null;
    }
    case 'system.notice':
      // Appended once per pause (design spec §5.4); the other notices are not the user's to act on.
      return ev.payload.code === WAKES_PAUSED ? { title, body: 'Agents are paused: too many automatic wakes this hour' } : null;
    default:
      return null;
  }
}

/** Posts notifications for new events while enabled and while no desktop client handles them. */
export function startNotifier(o: {
  store: EventStore;
  enabled(): boolean;
  suppressed(): boolean;
  post(n: Notification): void;
  onError(err: unknown): void;
}): () => void {
  return o.store.subscribe((item) => {
    if (item.kind !== 'event' || !o.enabled() || o.suppressed()) return;
    try {
      const n = notificationFor(item.event, o.store.db);
      if (n) o.post(n);
    } catch (err) {
      o.onError(err);
    }
  });
}

const SCRIPT = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run';

/** macOS notification via osascript; text is passed as arguments, never interpolated into the script. */
export function macNotify(n: Notification): void {
  execFile('osascript', ['-e', SCRIPT, truncate(n.title, 100), truncate(n.body, 240)], () => {});
}
