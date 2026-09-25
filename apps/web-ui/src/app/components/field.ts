import { ChangeDetectionStrategy, Component, input, ViewEncapsulation } from '@angular/core';

/** A labelled control: the label points at `id`, and an error replaces the hint. The control is projected. */
@Component({
  selector: 'div[deskField]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'field', '[attr.id]': 'null' },
  template: `
    <label [attr.for]="id()">{{ label() }}</label>
    <ng-content />
    @if (error()) {
      <p class="field-error" role="alert" [id]="id() + '-error'">{{ error() }}</p>
    } @else if (hint()) {
      <p class="field-hint" [id]="id() + '-hint'">{{ hint() }}</p>
    }
  `,
})
export class Field {
  readonly id = input.required<string>();
  readonly label = input.required<string>();
  readonly hint = input<string>();
  readonly error = input<string | null>();
}
