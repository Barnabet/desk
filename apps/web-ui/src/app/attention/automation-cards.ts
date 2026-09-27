import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal, untracked, ViewEncapsulation, type Signal } from '@angular/core';
import type { AttentionItem, AutomationDetail, RunDetail } from '@desk/protocol';
import { askDeskText, runFailure } from '@desk/ui-core';
import { TurnOnDialog } from '../automations/dialogs/turn-on-dialog';
import { AskAnswer, GateAnswer } from '../automations/runs/step-panel';
import { Button } from '../components/button';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { RouteService } from '../core/route.service';

export type AutomationItemData = { detail: AutomationDetail | null; run: RunDetail | null; missing: boolean };

/**
 * An item's automation and run, when it names them (both null until loaded; `missing` once either is gone): the
 * desktop's useAutomationItem. Call it in a field initializer. It asks again only when the ids change, not on every push.
 */
export function injectAutomationItem(item: () => AttentionItem): Signal<AutomationItemData> {
  const bridge = inject(DeskBridge);
  const state = signal<AutomationItemData>({ detail: null, run: null, missing: false });
  const automationId = computed(() => item().ref.automation_id);
  const runId = computed(() => item().ref.run_id);
  effect((onCleanup) => {
    const a = automationId();
    const r = runId();
    let live = true;
    onCleanup(() => {
      live = false;
    });
    untracked(() => {
      const gone = () => {
        if (live) state.update((s) => ({ ...s, missing: true }));
      };
      if (a) {
        bridge
          .call('automations.get', { id: a })
          .then((detail) => {
            if (live) state.update((s) => ({ ...s, detail }));
          })
          .catch(gone);
      }
      if (r) {
        bridge
          .call('automations.getRun', { runId: r })
          .then((run) => {
            if (live) state.update((s) => ({ ...s, run }));
          })
          .catch(gone);
      }
    });
  });
  return state.asReadonly();
}

/** The inspector's body for the four automation kinds (spec §8.4). */
@Component({
  selector: 'div[deskAutomationCard]',
  imports: [AskAnswer, Button, GateAnswer, TurnOnDialog],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @switch (item().kind) {
      @case ('automation_ask') {
        <h2 class="inspector-title">{{ askTitle() }}</h2>
        @if (auto().missing) {
          <p class="muted">{{ goneText }}</p>
        } @else {
          @if (auto().run; as run) {
            @if (row(); as row) {
              @if (row.status !== 'waiting') {
                <p class="muted">It has been answered.</p>
              } @else if (row.question) {
                <section deskAskAnswer [run]="run" [row]="row"></section>
              } @else if (row.gate) {
                <section deskGateAnswer [run]="run" [row]="row" [grantsSuspended]="auto().detail?.grants_suspended ?? true"></section>
              }
            } @else {
              <p class="muted">Loading the step…</p>
            }
          } @else {
            <p class="muted">Loading the step…</p>
          }
        }
        <div class="inspector-actions">
          <button deskButton variant="ghost" (click)="open.emit()">Open run <kbd>E</kbd></button>
        </div>
      }
      @case ('automation_failed') {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (item().detail) {
          <pre class="auto-error">{{ item().detail }}</pre>
        }
        @if (auto().missing) {
          <p class="muted">{{ goneText }}</p>
        }
        <div class="inspector-actions">
          <button deskButton variant="primary" (click)="open.emit()">Open run <kbd>E</kbd></button>
          <button deskButton [pending]="asking()" [disabled]="!auto().run" (click)="askFix()">Ask Desk to fix</button>
          <button deskButton [pending]="busy() === 'dismiss'" [disabled]="busy() !== null" (click)="dismiss.emit()">Dismiss</button>
        </div>
        <p class="muted small">Dismissing hides this here. The automation is not changed, and its next run starts on time.</p>
      }
      @case ('automation_enable_request') {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (item().detail) {
          <p>{{ item().detail }}</p>
        }
        @if (auto().missing) {
          <p class="muted">{{ goneText }}</p>
        }
        <div class="inspector-actions">
          <button deskButton variant="primary" [disabled]="!auto().detail" (click)="turningOn.set(true)">Turn on…</button>
          <button deskButton variant="ghost" (click)="open.emit()">Open automation <kbd>E</kbd></button>
          <button deskButton [pending]="busy() === 'dismiss'" [disabled]="busy() !== null" (click)="dismiss.emit()">Dismiss</button>
        </div>
        <p class="muted small">Only you turn automations on. Dismissing leaves it off; Desk is not told.</p>
        @if (turningOn()) {
          @if (auto().detail; as detail) {
            <div deskTurnOnDialog [detail]="detail" (close)="turningOn.set(false)" (done)="turningOn.set(false)" (testFirst)="testFirst()"></div>
          }
        }
      }
      @default {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (item().detail) {
          <p>{{ item().detail }}</p>
        }
        <div class="inspector-actions">
          <button deskButton variant="primary" (click)="open.emit()">Review changes <kbd>E</kbd></button>
          <button deskButton [pending]="busy() === 'dismiss'" [disabled]="busy() !== null" (click)="dismiss.emit()">Dismiss</button>
        </div>
        <p class="muted small">Until you keep them on its Grants tab, its runs ask you for everything.</p>
      }
    }
  `,
})
export class AutomationCard {
  readonly item = input.required<AttentionItem>();
  readonly auto = input.required<AutomationItemData>();
  readonly busy = input<string | null>(null);
  readonly open = output<void>();
  readonly dismiss = output<void>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  protected readonly goneText = 'It could not be loaded: the automation or its run may have been deleted.';
  protected readonly turningOn = signal(false);
  protected readonly asking = signal(false);
  protected readonly row = computed(() => {
    const run = this.auto().run;
    const stepId = this.item().ref.step_id;
    return run && stepId ? run.steps.find((s) => s.step_id === stepId) : undefined;
  });
  protected readonly askTitle = computed(() => {
    const run = this.auto().run;
    const row = this.row();
    const stepTitle = row ? (run?.definition.steps.find((s) => s.id === row.step_id)?.title ?? row.step_id) : null;
    return run && stepTitle ? `${run.automation_title} · ${stepTitle}` : this.item().title;
  });

  protected async askFix(): Promise<void> {
    const run = this.auto().run;
    if (!run) return;
    const projectId = this.item().project_id;
    this.asking.set(true);
    try {
      const f = runFailure(run);
      await this.bridge.call('projects.send', { id: projectId, text: askDeskText(run, f.stepTitle, f.error) });
      this.toasts.toast({ tone: 'info', message: 'Sent to Desk.', action: { label: 'Open conversation', run: () => this.routes.navigate({ name: 'project', id: projectId, tab: 'conversation' }) } });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.asking.set(false);
    }
  }

  protected testFirst(): void {
    this.turningOn.set(false);
    this.open.emit();
  }
}
