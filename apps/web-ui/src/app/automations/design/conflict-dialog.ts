import { booleanAttribute, ChangeDetectionStrategy, Component, computed, input, output, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, AutomationDetail } from '@desk/protocol';
import { diffDefinitions } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Sheet } from '../../components/sheet';
import { DiffView } from '../versions/diff-view';

/** A save refused because another version landed first: that version, and who saved it ("Desk", "You (CLI)"…). */
export type Conflict = { theirs: AutomationDetail; by: string };

/** Spec §8.2: "Desk saved v8 while you were editing", with Review changes, Save mine anyway and Discard mine. */
@Component({
  selector: 'div[deskConflictDialog]',
  imports: [Button, DiffView, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="title()" [width]="640" (close)="close.emit()">
      <p>{{ 'Saved by ' + conflict().by + '. Your edits started from an earlier version.' }}</p>
      <p class="muted small">{{ 'Save mine anyway makes your version v' + (v() + 1) + '; v' + v() + ' stays in Versions. Discard mine loads v' + v() + ' into the editor.' }}</p>
      @if (review()) {
        <section aria-label="What saving yours changes">
          <div deskDiffView [diff]="diff()" [labels]="labels()"></div>
        </section>
      }
      <div class="sheet-footer">
        <button deskButton (click)="review.set(!review())">{{ review() ? 'Hide changes' : 'Review changes' }}</button>
        <button deskButton (click)="discardMine.emit()">Discard mine</button>
        <button deskButton variant="primary" [pending]="saving()" (click)="saveMine.emit()">Save mine anyway</button>
      </div>
    </div>
  `,
})
export class ConflictDialog {
  readonly conflict = input.required<Conflict>();
  readonly mine = input.required<AutomationDefinition>();
  readonly saving = input(false, { transform: booleanAttribute });
  readonly saveMine = output<void>();
  readonly discardMine = output<void>();
  readonly close = output<void>();
  protected readonly review = signal(false);
  protected readonly v = computed(() => this.conflict().theirs.version);
  protected readonly title = computed(() => (this.conflict().by === 'Desk' ? `Desk saved v${this.v()} while you were editing` : `v${this.v()} was saved while you were editing`));
  protected readonly diff = computed(() => diffDefinitions(this.conflict().theirs.definition, this.mine()));
  protected readonly labels = computed(() => ({ before: `v${this.v()}`, after: 'yours' }));
}
