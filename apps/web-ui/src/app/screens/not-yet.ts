import { ChangeDetectionStrategy, Component, input, ViewEncapsulation } from '@angular/core';
import { EmptyState } from '../components/empty-state';

/** A place the web UI has not ported yet (spec §6 phases): what exists, and where it lives meanwhile. */
@Component({
  selector: 'div[deskNotYet]',
  imports: [EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  template: `<div deskEmptyState [title]="label() + ' is not in the web UI yet'" body="It comes in a later phase. The Desk desktop app has it today, and both show the same projects and threads."></div>`,
})
export class NotYet {
  readonly label = input.required<string>();
}
