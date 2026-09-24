import type { AgentStatus } from '@desk/protocol';

export type StatusTone = 'run' | 'wait' | 'done' | 'fail' | 'idle';

/** Calm resilience wording: a restart or a proxy outage reads as "will resume", never as an error. */
export function statusLabel(status: AgentStatus, reason?: string | null, proxyDown = false): { label: string; tone: StatusTone } {
  switch (status) {
    case 'running':
      return proxyDown ? { label: 'Paused, will resume', tone: 'wait' } : { label: 'Running', tone: 'run' };
    case 'queued':
      return /restart|shut ?down/i.test(reason ?? '') ? { label: 'Will resume', tone: 'wait' } : { label: 'Queued', tone: 'wait' };
    case 'waiting':
      return { label: 'Waiting', tone: 'wait' };
    case 'done':
      return { label: 'Done', tone: 'done' };
    case 'failed':
      return { label: 'Failed', tone: 'fail' };
    case 'cancelled':
      return { label: 'Stopped', tone: 'done' };
    default:
      return { label: 'Idle', tone: 'idle' };
  }
}

export function StatusChip({ status, reason, proxyDown }: { status: AgentStatus; reason?: string | null; proxyDown?: boolean }) {
  const s = statusLabel(status, reason, proxyDown);
  return (
    <span className={`chip chip-${s.tone}`} {...(reason ? { title: reason } : {})}>
      {s.label}
    </span>
  );
}
