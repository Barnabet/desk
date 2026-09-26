import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, output } from '@angular/core';
import { agentTitle, type MessagesState, type MessageView } from '@desk/client';
import { clock, duration, href, pairView } from '@desk/ui-core';
import { NowService } from '../core/now.service';
import { Button } from './button';
import { SafeMarkdown } from './safe-markdown';
import { Sheet } from './sheet';

/**
 * The messages between two agents (design spec §8 item 8), oldest first, each answer nested under its question with
 * the question's state. It lives in its opener's local state (no route) and reads the session's fold, so a question's
 * state changes in place when its answer arrives. The host adds no box; the sheet moves itself into document.body.
 */
@Component({
  selector: 'div[deskPairSheet]',
  imports: [NgTemplateOutlet, Sheet, Button, SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <!-- One message: who wrote to whom, its text, a question's state, and where it shows in a transcript. -->
    <ng-template #entry let-e>
      <div class="pair-msg" [class.muted]="e.message.auto">
        <span class="pair-head">{{ head(e.message) }}</span>
        <div deskSafeMarkdown [className]="'pair-text'" [text]="e.message.text"></div>
        <span class="pair-foot">
          @if (e.message.state) {
            @switch (e.message.state) {
              @case ('open') {
                <span class="pair-state pair-open">open · {{ openFor(e.message) }}</span>
              }
              @case ('answered') {
                <span class="pair-state">answered{{ e.message.stateAt ? ' ' + clock(e.message.stateAt) : '' }}</span>
              }
              @default {
                <span class="pair-state muted">{{ e.message.state }}</span>
              }
            }
          }
          @if (e.shownIn) {
            <!-- Following the link closes the sheet; the thread opens at the stop that holds the message (?at=). -->
            <a [href]="transcriptHref(e.shownIn, e.message.id)" (click)="close.emit()">show in {{ title(e.shownIn) }} transcript</a>
          }
        </span>
      </div>
    </ng-template>
    <div deskSheet [title]="view().title" [width]="600" (close)="close.emit()">
      @if (view().rows.length) {
        <ol class="pair-rows">
          @for (r of view().rows; track r.message.id) {
            <li>
              <ng-container *ngTemplateOutlet="entry; context: { $implicit: r }" />
              @if (r.answers.length) {
                <ol class="pair-answers">
                  @for (x of r.answers; track x.message.id) {
                    <li><ng-container *ngTemplateOutlet="entry; context: { $implicit: x }" /></li>
                  }
                </ol>
              }
            </li>
          }
        </ol>
      } @else {
        <p class="muted">They have not written to each other yet.</p>
      }
      <div class="sheet-footer"><button deskButton size="sm" (click)="close.emit()">Close</button></div>
    </div>
  `,
})
export class PairSheet {
  readonly projectId = input.required<string>();
  readonly messages = input.required<MessagesState>();
  readonly a = input.required<string>();
  readonly b = input.required<string>();
  readonly close = output<void>();
  private readonly now = inject(NowService).now;
  protected readonly view = computed(() => pairView(this.messages(), this.a(), this.b()));
  protected readonly clock = clock;

  /** "Auth API → Frontend · question · 14:05"; a closure is the runtime's, written when the thread could not answer: never shown as an answer. */
  protected head(m: MessageView): string {
    const from = agentTitle(this.messages(), m.from);
    return `${m.auto ? `${from} could not answer` : `${from} → ${agentTitle(this.messages(), m.to)} · ${m.kind}`} · ${clock(m.ts)}`;
  }

  protected openFor(m: MessageView): string {
    return duration(Math.max(0, this.now() - Date.parse(m.ts)));
  }

  protected title(id: string): string {
    return agentTitle(this.messages(), id);
  }

  protected transcriptHref(threadId: string, at: number): string {
    return href({ name: 'project', id: this.projectId(), tab: 'threads', threadId, at });
  }
}
