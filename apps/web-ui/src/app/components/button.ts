import { booleanAttribute, ChangeDetectionStrategy, Component, computed, input, ViewEncapsulation } from '@angular/core';

/** A real button; `pending` disables it and marks it busy until the write completes. */
@Component({
  selector: 'button[deskButton]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    class: 'btn',
    '[class]': 'variantClass()',
    '[attr.type]': 'type()',
    '[disabled]': 'disabled() || pending()',
    '[attr.aria-busy]': "pending() ? 'true' : null",
  },
  template: '<ng-content />',
})
export class Button {
  readonly variant = input<'primary' | 'secondary' | 'ghost' | 'danger'>('secondary');
  readonly size = input<'sm' | 'md'>('md');
  readonly pending = input(false, { transform: booleanAttribute });
  readonly disabled = input(false, { transform: booleanAttribute });
  readonly type = input<'button' | 'submit' | 'reset'>('button');
  protected readonly variantClass = computed(() => `btn-${this.variant()}${this.size() === 'sm' ? ' btn-sm' : ''}`);
}
