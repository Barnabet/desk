import { ChangeDetectionStrategy, Component, computed, effect, inject, input, linkedSignal, output, signal, untracked, ViewEncapsulation } from '@angular/core';
import type { AutomationDetail } from '@desk/protocol';
import { dayTime, describeGrant, describeSchedule, sameGrant, uniqueGrants } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Sheet } from '../../components/sheet';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';

/**
 * Turning an automation on (spec §5.4): its schedules with their next time, the grants its runs were approved for
 * (ticked), and a warning when the current version has no succeeded test. Grants are set first, then the switch.
 */
@Component({
  selector: 'div[deskTurnOnDialog]',
  imports: [Button, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="'Turn on ' + detail().title" [width]="560" (close)="close.emit()">
      @if (untested()) {
        <p class="auto-warn" role="alert">{{ warning() }}</p>
      }
      <h3 class="auto-sub">When it runs</h3>
      @if (detail().definition.triggers.length) {
        <ul class="auto-plain">
          @for (t of detail().definition.triggers; track $index; let i = $index) {
            <li>{{ describeSchedule(t.cron, t.timezone) }}@if (firstNext(i); as first) {<span class="muted">{{ ' · next ' + dayTime(first, now()) }}</span>}</li>
          }
        </ul>
      } @else {
        <p class="muted">It has no schedule, so turning it on changes nothing until you add one. Run now works either way.</p>
      }
      @if (detail().enable_request?.note; as note) {
        <blockquote class="auto-quote">{{ 'Desk: ' + note }}</blockquote>
      }
      <h3 class="auto-sub">Grants</h3>
      @if (detail().grants.length) {
        <ul class="auto-plain">
          @for (g of detail().grants; track $index) {
            <li>{{ describeGrant(g) }} <span class="muted">(kept)</span></li>
          }
        </ul>
      }
      @if (proposals().length) {
        <p class="muted small">Approved during its runs. Ticked ones let later runs go ahead without asking you.</p>
        @for (g of proposals(); track $index; let i = $index) {
          <label class="auto-check"><input type="checkbox" [checked]="ticked()[i] ?? false" (change)="tick(i, $event)" /> {{ describeGrant(g) }}</label>
        }
      } @else if (!detail().grants.length) {
        <p class="muted">No grants: anything its steps need approval for will ask you when it runs.</p>
      }
      <div class="sheet-footer">
        <button deskButton (click)="close.emit()">Cancel</button>
        @if (untested()) {
          <button deskButton (click)="testFirst.emit()">Test first</button>
        }
        <button deskButton variant="primary" [pending]="pending()" (click)="turnOn()">{{ untested() ? 'Turn on anyway' : 'Turn on' }}</button>
      </div>
    </div>
  `,
})
export class TurnOnDialog {
  readonly detail = input.required<AutomationDetail>();
  readonly close = output<void>();
  /** React's onDone: the automation, now on. */
  readonly done = output<AutomationDetail>();
  /** React's onTestFirst. */
  readonly testFirst = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly now = inject(NowService).now;
  protected readonly dayTime = dayTime;
  protected readonly describeGrant = describeGrant;
  protected readonly describeSchedule = describeSchedule;
  protected readonly proposals = computed(() => {
    const d = this.detail();
    return uniqueGrants([...(d.enable_request?.proposed_grants ?? []), ...d.proposed_grants]).filter((g) => !d.grants.some((x) => sameGrant(x, g)));
  });
  protected readonly ticked = linkedSignal(() => untracked(() => this.proposals().map(() => true)));
  protected readonly pending = signal(false);
  private readonly next = signal<Record<string, string[]>>({});
  protected readonly untested = computed(() => this.detail().tested_version !== this.detail().version);
  protected readonly warning = computed(() => {
    const d = this.detail();
    return `v${d.version} hasn't been tested${d.tested_version ? ` (v${d.tested_version} was)` : ''}.`;
  });
  /** What validate needs, compared by value, so a refreshed detail with the same definition does not ask again. */
  private readonly check = computed(
    () => {
      const d = this.detail();
      return { projectId: d.project_id, definition: d.definition, name: d.name };
    },
    { equal: (a, b) => a.projectId === b.projectId && a.definition === b.definition && a.name === b.name },
  );
  private seq = 0;

  constructor() {
    effect(() => {
      const c = this.check();
      const n = ++this.seq;
      this.bridge
        .call('automations.validate', { projectId: c.projectId, req: { definition: c.definition, name: c.name } })
        .then((r) => {
          if (n === this.seq) this.next.set(r.next_times);
        })
        .catch(() => {});
    });
  }

  protected firstNext(i: number): string | undefined {
    return this.next()[String(i)]?.[0];
  }

  protected tick(i: number, e: Event): void {
    const on = (e.target as HTMLInputElement).checked;
    this.ticked.update((t) => t.map((v, j) => (j === i ? on : v)));
  }

  protected async turnOn(): Promise<void> {
    const d = this.detail();
    this.pending.set(true);
    try {
      const grants = uniqueGrants([...d.grants, ...this.proposals().filter((_, i) => this.ticked()[i])]);
      await this.bridge.call('automations.setGrants', { id: d.id, grants, reason: 'enabled' });
      this.done.emit(await this.bridge.call('automations.setEnabled', { id: d.id, enabled: true }));
    } catch (err) {
      this.toasts.error(err);
      this.pending.set(false);
    }
  }
}
