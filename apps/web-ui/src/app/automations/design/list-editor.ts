import { ChangeDetectionStrategy, Component, input, output, ViewEncapsulation } from '@angular/core';
import type { TemplateSuggestion } from '@desk/ui-core';
import { Button } from '../../components/button';
import { TemplateField } from './template-field';

/** A list of short texts (routes, globs, arguments): one row each, Remove per row, and Add. Rows are labelled "<label> <n>". */
@Component({
  selector: 'fieldset[deskListEditor]',
  imports: [Button, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'field auto-rows', '[attr.id]': 'null' },
  template: `
    <legend>{{ label() }}</legend>
    @for (v of values(); track $index; let i = $index) {
      <div>
        <div class="auto-row">
          @if (suggestions(); as sug) {
            <div deskTemplateField [id]="id() + '-' + i" [label]="label() + ' ' + (i + 1)" [labelHidden]="true" [value]="v" [suggestions]="sug" [placeholder]="placeholder()" (valueChange)="set(i, $event)"></div>
          } @else {
            <input class="input mono" [id]="id() + '-' + i" [attr.aria-label]="label() + ' ' + (i + 1)" [value]="v" [attr.placeholder]="placeholder() ?? null" (input)="set(i, val($event))" />
          }
          <button deskButton size="sm" variant="ghost" [attr.aria-label]="'Remove ' + label() + ' ' + (i + 1)" (click)="remove(i)">✕</button>
        </div>
        @if (problem(v); as p) {
          <p class="field-error auto-row-error">{{ p }}</p>
        }
      </div>
    }
    @if (max() === undefined || values().length < max()!) {
      <div><button deskButton size="sm" (click)="valuesChange.emit([...values(), ''])">{{ addLabel() ?? 'Add' }}</button></div>
    }
    @if (hint()) {
      <p class="field-hint">{{ hint() }}</p>
    }
  `,
})
export class ListEditor {
  readonly id = input.required<string>();
  readonly label = input.required<string>();
  readonly values = input.required<string[]>();
  readonly placeholder = input<string | undefined>(undefined);
  readonly hint = input<string | undefined>(undefined);
  readonly addLabel = input<string | undefined>(undefined);
  /** Checks one value; its message shows under that row. */
  readonly check = input<((value: string) => string | null) | undefined>(undefined);
  /** Makes each row a template field (script arguments). */
  readonly suggestions = input<TemplateSuggestion[] | undefined>(undefined);
  readonly max = input<number | undefined>(undefined);
  /** React's onChange. */
  readonly valuesChange = output<string[]>();

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected set(i: number, v: string): void {
    this.valuesChange.emit(this.values().map((x, j) => (j === i ? v : x)));
  }

  protected remove(i: number): void {
    this.valuesChange.emit(this.values().filter((_, j) => j !== i));
  }

  protected problem(v: string): string | null {
    const check = this.check();
    return v && check ? check(v) : null;
  }
}
