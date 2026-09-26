import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, input } from '@angular/core';
import { href, type WaitHop } from '@desk/ui-core';

/** One hop past a wait: a vermilion dot and "→ needs your approval", linking to that attention item (design spec §8 item 11). */
@Component({
  selector: 'a[deskHopLink]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'wait-hop', '[href]': 'link()', '[attr.aria-label]': 'hop().label' },
  template: `<span class="hop-dot" aria-hidden="true"></span>{{ hop().text }}`,
})
export class HopLink {
  readonly hop = input.required<WaitHop>();
  protected readonly link = computed(() => href({ name: 'attention', item: this.hop().item.id }));
}
