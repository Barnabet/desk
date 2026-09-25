import { booleanAttribute, ChangeDetectionStrategy, Component, input, output, ViewEncapsulation } from '@angular/core';
import { Button } from './button';
import { Sheet } from './sheet';

/** A Sheet that asks one question: Cancel, or the confirm button (red when `danger`). */
@Component({
  selector: 'div[deskConfirmDialog]',
  imports: [Button, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents', '[attr.title]': 'null' },
  template: `
    <div deskSheet [title]="title()" [width]="460" (close)="cancel.emit()">
      <ng-content />
      <div class="sheet-footer">
        <button deskButton (click)="cancel.emit()">Cancel</button>
        <button deskButton [variant]="danger() ? 'danger' : 'primary'" (click)="confirm.emit()">{{ confirmLabel() }}</button>
      </div>
    </div>
  `,
})
export class ConfirmDialog {
  readonly title = input.required<string>();
  readonly confirmLabel = input.required<string>();
  readonly danger = input(false, { transform: booleanAttribute });
  readonly confirm = output<void>();
  readonly cancel = output<void>();
}
