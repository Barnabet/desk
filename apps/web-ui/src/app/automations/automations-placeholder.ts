import { ChangeDetectionStrategy, Component, input, ViewEncapsulation } from '@angular/core';
import { EmptyState } from '../components/empty-state';

/** The Automations tab until Plan 21 ports the desktop's screens (AutomationsScreen.tsx). */
@Component({
  selector: 'desk-automations-placeholder',
  imports: [EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  template: `<div deskEmptyState title="Automations" body="Automations open in the Desk app for now. The web UI gets them next."></div>`,
})
export class AutomationsPlaceholder {
  readonly projectId = input.required<string>();
}
