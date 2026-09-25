import { ChangeDetectionStrategy, Component, ViewEncapsulation } from '@angular/core';
import { EmptyState } from './empty-state';

/** The page without a session secret (spec §2): nothing of Desk, only how to open it. */
@Component({
  selector: 'div[deskSignedOut]',
  imports: [EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  template: `
    <div
      deskEmptyState
      title="Open Desk from your terminal"
      body="Run desk web on this computer and open the link it prints. A link signs this browser in once and expires after two minutes; if desk web is already running, press Enter in its terminal for a new one."
    ></div>
  `,
})
export class SignedOut {}
