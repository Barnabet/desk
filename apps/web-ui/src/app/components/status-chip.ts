import { booleanAttribute, ChangeDetectionStrategy, Component, computed, input, ViewEncapsulation } from '@angular/core';
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

@Component({
  selector: 'span[deskStatusChip]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'chip', '[class]': "'chip-' + view().tone", '[attr.title]': 'reason() || null' },
  template: '{{ view().label }}',
})
export class StatusChip {
  readonly status = input.required<AgentStatus>();
  readonly reason = input<string | null>();
  readonly proxyDown = input(false, { transform: booleanAttribute });
  protected readonly view = computed(() => statusLabel(this.status(), this.reason(), this.proxyDown()));
}
