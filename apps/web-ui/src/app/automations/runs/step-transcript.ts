import { ChangeDetectionStrategy, Component, computed, input, output, signal, ViewEncapsulation } from '@angular/core';
import { narrate, sentCalls } from '@desk/ui-core';
import { Sheet } from '../../components/sheet';
import { transcriptOf, type SessionState } from '../../core/session.service';
import { Transcript, type ComposerMode, type Depth } from '../../threads/transcript';

/** A step agent's transcript (the Threads tab's view), which only the run view opens: step agents are not threads. */
@Component({
  selector: 'div[deskStepTranscript]',
  imports: [Sheet, Transcript],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents', '[attr.title]': 'null' },
  template: `
    <div deskSheet [title]="title() + ' · transcript'" [width]="760" (close)="close.emit()">
      <div class="auto-transcript">
        <aside
          deskTranscript
          [projectId]="projectId()"
          [threadId]="agentId()"
          [rows]="rows()"
          [entries]="transcript().entries"
          [reviewRounds]="0"
          [messages]="s().messages"
          [sent]="sent()"
          [selected]="selected()"
          [(depth)]="depth"
          [composer]="composer"
          (selectStop)="selected.set($event)"
        ></aside>
      </div>
    </div>
  `,
})
export class StepTranscript {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly agentId = input.required<string>();
  readonly title = input.required<string>();
  readonly close = output<void>();
  protected readonly transcript = computed(() => transcriptOf(this.s().events, this.s().streams[this.agentId()], this.projectId(), this.agentId()));
  protected readonly sent = computed(() => sentCalls(this.s().messages, this.agentId()));
  protected readonly rows = computed(() => narrate(this.transcript().entries, this.sent()));
  protected readonly selected = signal<number | null>(null);
  protected readonly depth = signal<Depth>('narrative');
  protected readonly composer: ComposerMode = { kind: 'off', hint: 'A step agent takes no messages. Answer its approvals here, or stop the step.' };
}
