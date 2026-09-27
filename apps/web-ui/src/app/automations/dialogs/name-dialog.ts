import { ChangeDetectionStrategy, Component, computed, input, linkedSignal, output, untracked, ViewEncapsulation } from '@angular/core';
import { AutomationName } from '@desk/protocol';
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { Sheet } from '../../components/sheet';

/** Asks for an automation name (fixed at creation): Blank automation, and importing under another name. */
@Component({
  selector: 'div[deskNameDialog]',
  imports: [Button, Field, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents', '[attr.title]': 'null' },
  template: `
    <div deskSheet [title]="title()" [width]="460" (close)="close.emit()">
      <form (submit)="submit($event)">
        <div deskField id="automation-name" label="Name" [hint]="hint() ?? defaultHint" [error]="error()">
          <input id="automation-name" class="input mono" [value]="name()" (input)="setName($event)" />
        </div>
      </form>
      <div class="sheet-footer">
        <button deskButton (click)="close.emit()">Cancel</button>
        <button deskButton variant="primary" [disabled]="!ok()" (click)="confirm.emit(name())">{{ confirmLabel() }}</button>
      </div>
    </div>
  `,
})
export class NameDialog {
  readonly title = input.required<string>();
  readonly confirmLabel = input.required<string>();
  readonly initial = input('');
  readonly taken = input.required<string[]>();
  readonly hint = input<string | undefined>(undefined);
  readonly close = output<void>();
  readonly confirm = output<string>();

  protected readonly defaultHint = 'Lowercase letters, digits and dashes, e.g. weekly-digest. The name cannot change later; the title can.';
  protected readonly name = linkedSignal(() => untracked(() => this.initial()));
  protected readonly error = computed(() => {
    const name = this.name();
    if (!name) return null;
    const parsed = AutomationName.safeParse(name);
    if (!parsed.success) return parsed.error.issues[0]?.message ?? 'Not a valid name';
    return this.taken().includes(name) ? 'Another automation already has this name.' : null;
  });
  protected readonly ok = computed(() => this.name() !== '' && this.error() === null);

  /** Lowercase, spaces as dashes, as it is typed (React's controlled input). */
  protected setName(e: Event): void {
    const el = e.target as HTMLInputElement;
    const v = el.value.toLowerCase().replace(/\s+/g, '-');
    if (el.value !== v) el.value = v;
    this.name.set(v);
  }

  protected submit(e: Event): void {
    e.preventDefault();
    if (this.ok()) this.confirm.emit(this.name());
  }
}
