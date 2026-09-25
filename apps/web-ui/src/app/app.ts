import { ChangeDetectionStrategy, Component, ViewEncapsulation } from '@angular/core';
import { initialGlobalState } from '@desk/bff/contract';
import { href } from '@desk/ui-core';

/** The root component. For now it only proves that the shared packages load in the browser build. */
@Component({
  selector: 'desk-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  template: `<p>Desk · {{ status }} · {{ map }}</p>`,
})
export class App {
  protected readonly status = initialGlobalState().connection.status;
  protected readonly map = href({ name: 'map' });
}
