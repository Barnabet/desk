import { ChangeDetectionStrategy, Component, input, ViewEncapsulation } from '@angular/core';

/** A calm empty or failed state: a title, an optional line, and the projected action. */
@Component({
  selector: 'div[deskEmptyState]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'empty', '[attr.title]': 'null' },
  template: `
    <h2>{{ title() }}</h2>
    @if (body()) {
      <p class="subtitle">{{ body() }}</p>
    }
    <ng-content />
  `,
})
export class EmptyState {
  readonly title = input.required<string>();
  readonly body = input<string | null>();
}
