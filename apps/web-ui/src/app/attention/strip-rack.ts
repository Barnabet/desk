import { ChangeDetectionStrategy, Component, input, output, ViewEncapsulation } from '@angular/core';
import type { AttentionBays } from '@desk/client';
import { BAYS } from '@desk/ui-core';
import { FlightStrip } from './flight-strip';

/** The rack: four bays, each a recessed tray holding its strips. */
@Component({
  selector: 'section[deskStripRack]',
  imports: [FlightStrip],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'rack', 'aria-label': 'Strip rack' },
  template: `
    @for (b of bayList; track b.key) {
      <div class="bay" role="group" [attr.aria-label]="b.name + ': ' + b.sub + ', ' + bays()[b.key].length">
        <div class="bay-name"><span class="bay-title">{{ b.name }}</span><span class="bay-sub">{{ b.sub }} · {{ bays()[b.key].length }}</span></div>
        <div class="bay-tray">
          @for (i of bays()[b.key]; track i.id) {
            <button deskFlightStrip [item]="i" [now]="now()" [selected]="i.id === selectedId()" [threadTitle]="threadTitle()" (pick)="pick.emit(i.id)"></button>
          } @empty {
            <span class="bay-empty">Nothing here</span>
          }
        </div>
      </div>
    }
  `,
})
export class StripRack {
  readonly bays = input.required<AttentionBays>();
  readonly now = input.required<number>();
  readonly selectedId = input<string | null>(null);
  readonly threadTitle = input.required<(id: string) => string | null>();
  /** React's onSelect: the picked strip's item id. */
  readonly pick = output<string>();
  protected readonly bayList = BAYS;
}
