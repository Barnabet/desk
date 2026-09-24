import { execFile } from 'node:child_process';
import type { StoredEvent } from '@desk/protocol';
import { getAgent, getProject, type Db, type EventStore } from '@desk/core';

export type Notification = { title: string; body: string };

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** What (if anything) to tell the user about an event when no desktop client is showing notifications. */
export function notificationFor(ev: StoredEvent, db: Db): Notification | null {
  const project = getProject(db, ev.project_id);
  if (!project) return null;
  const title = `Desk · ${project.name}`;
  const name = (id: string | null) => {
    const a = id ? getAgent(db, id) : undefined;
    return a?.role === 'desk' ? 'Desk' : (a?.title ?? 'A thread');
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
