import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, input } from '@angular/core';
import type { ProjectState } from '@desk/client';
import { ago, plural } from '@desk/ui-core';
import { SafeMarkdown } from '../components/safe-markdown';

/** Desk's What's up at the top of the conversation's left column; until Desk has written one, the thread counts. */
@Component({
  selector: 'section[deskWhatsUp]',
  imports: [SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'whats-up', 'aria-label': "What's up" },
  template: `
    <span class="eyebrow">{{ eyebrow() }}</span>
    @if (project().whatsUp; as w) {
      <div deskSafeMarkdown [className]="'md-voice whats-up-text'" [text]="w.text"></div>
    } @else {
      <p class="muted">Desk and {{ plural(live().length, 'thread') }}. {{ count('running') }} running, {{ count('waiting') }} waiting, {{ count('done') }} done.</p>
    }
  `,
})
export class WhatsUp {
  readonly project = input.required<ProjectState>();
  readonly now = input.required<number>();
  protected readonly plural = plural;
  protected readonly live = computed(() => this.project().threads.filter((t) => !t.archived_at));
  protected readonly eyebrow = computed(() => {
    const w = this.project().whatsUp;
    const when = w ? ago(w.ts, this.now()) : null;
    return `What's up${when ? ` · ${when === 'now' ? 'just now' : `${when} ago`}` : ''}`;
  });

  protected count(status: string): number {
    return this.live().filter((t) => t.status === status).length;
  }
}
