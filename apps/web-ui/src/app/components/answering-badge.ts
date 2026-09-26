import { ChangeDetectionStrategy, Component, ViewEncapsulation, input } from '@angular/core';

/** "answering Frontend" with a live dot: a thread's answer run in progress. Its status chip does not change (design spec §8 item 3). */
@Component({
  selector: 'span[deskAnsweringBadge]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'answering-badge' },
  template: `<span class="live-dot" aria-hidden="true"></span>{{ label() }}`,
})
export class AnsweringBadge {
  readonly label = input.required<string>();
}
