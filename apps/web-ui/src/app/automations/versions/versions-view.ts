import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, AutomationDetail, AutomationVersionInfo } from '@desk/protocol';
import { dayTime, diffDefinitions, originText } from '@desk/ui-core';
import { Button } from '../../components/button';
import { ConfirmDialog } from '../../components/confirm-dialog';
import { EmptyState } from '../../components/empty-state';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';
import { DiffView } from './diff-view';

/** Versions (spec §8.4): who saved each and when, its note and test, a diff between any two, and Restore. */
@Component({
  selector: 'div[deskVersionsView]',
  imports: [Button, ConfirmDialog, DiffView, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (versions(); as list) {
      @if (list.length) {
        <div class="auto-versions">
          <ul class="auto-version-list">
            @for (v of list; track v.version) {
              <li class="auto-version">
                <div class="auto-version-head">
                  <b class="mono">{{ 'v' + v.version }}</b>
                  <span>{{ originText(v) }}</span>
                  <span class="muted small">{{ dayTime(v.created_at, now()) }}</span>
                  @if (v.tested) {
                    <span class="auto-tag added">✓ tested</span>
                  }
                  @if (v.version === detail().version) {
                    <span class="auto-tag">current</span>
                  }
                  <span class="grow"></span>
                  @if (v.version !== detail().version) {
                    <button deskButton size="sm" variant="ghost" (click)="restoring.set(v.version)">Restore…</button>
                  }
                </div>
                @if (v.change_note) {
                  <p class="auto-version-note">{{ v.change_note }}</p>
                }
              </li>
            }
          </ul>
          <section class="auto-version-diff" [attr.aria-label]="'Changes from v' + from() + ' to v' + to()">
            <div class="auto-inline">
              <label class="auto-inline small" for="versions-from"
                >Compare<select id="versions-from" class="select" (change)="from.set(num($event))">
                  @for (v of list; track v.version) {
                    <option [value]="'' + v.version" [selected]="v.version === from()">{{ 'v' + v.version }}</option>
                  }
                </select></label
              >
              <label class="auto-inline small" for="versions-to"
                >with<select id="versions-to" class="select" (change)="to.set(num($event))">
                  @for (v of list; track v.version) {
                    <option [value]="'' + v.version" [selected]="v.version === to()">{{ 'v' + v.version }}</option>
                  }
                </select></label
              >
            </div>
            @if (diff(); as d) {
              <div deskDiffView [diff]="d" [labels]="labels()"></div>
            } @else {
              <p class="muted">Loading…</p>
            }
          </section>
          @if (restoring() !== null) {
            <div deskConfirmDialog [title]="'Restore v' + restoring() + '?'" confirmLabel="Restore" (confirm)="restore(restoring() ?? 0)" (cancel)="restoring.set(null)">{{ restoreNote() }}</div>
          }
        </div>
      } @else {
        <div deskEmptyState title="No versions" body="Saving the automation creates its first version."></div>
      }
    } @else {
      <p class="muted auto-loading">Loading…</p>
    }
  `,
})
export class VersionsView {
  readonly detail = input.required<AutomationDetail>();
  /** React's onChange: the automation after a restore. */
  readonly detailChange = output<AutomationDetail>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly now = inject(NowService).now;
  protected readonly originText = originText;
  protected readonly dayTime = dayTime;
  protected readonly versions = signal<AutomationVersionInfo[] | null>(null);
  protected readonly from = signal(0);
  protected readonly to = signal(0);
  protected readonly restoring = signal<number | null>(null);
  /** Definitions by `<automation id>:<version>`, each asked for once. */
  private readonly defs = signal<Record<string, AutomationDefinition>>({});
  private readonly asked = new Set<string>();
  private readonly listKey = computed(() => `${this.detail().id}:${this.detail().version}`);
  protected readonly diff = computed(() => {
    const id = this.detail().id;
    const a = this.defs()[`${id}:${this.from()}`];
    const b = this.defs()[`${id}:${this.to()}`];
    return a && b ? diffDefinitions(a, b) : null;
  });
  protected readonly labels = computed(() => ({ before: `v${this.from()}`, after: `v${this.to()}` }));
  protected readonly restoreNote = computed(() => {
    const v = this.detail().version;
    return `Its definition becomes a new version, v${v + 1}. Nothing is lost: v${v} stays in the list.`;
  });
  private seq = 0;

  constructor() {
    // The list, again after each new version.
    effect(() => {
      this.listKey();
      const id = this.detail().id;
      const n = ++this.seq;
      this.bridge
        .call('automations.versions', { id })
        .then((list) => {
          if (n !== this.seq) return;
          this.versions.set(list);
          this.to.set(list[0]?.version ?? 0);
          this.from.set(list[1]?.version ?? list[0]?.version ?? 0);
        })
        .catch((err: unknown) => this.toasts.error(err));
    });
    // The two compared versions' definitions.
    effect(() => {
      const id = this.detail().id;
      for (const v of [this.from(), this.to()]) {
        const key = `${id}:${v}`;
        if (v <= 0 || this.asked.has(key)) continue;
        this.asked.add(key);
        this.bridge
          .call('automations.version', { id, version: v })
          .then((r) => this.defs.update((d) => ({ ...d, [key]: r.definition })))
          .catch((err: unknown) => {
            this.asked.delete(key);
            this.toasts.error(err);
          });
      }
    });
  }

  protected num(e: Event): number {
    return Number((e.target as HTMLSelectElement).value);
  }

  protected async restore(version: number): Promise<void> {
    this.restoring.set(null);
    try {
      const next = await this.bridge.call('automations.restore', { id: this.detail().id, version });
      this.toasts.toast({ tone: 'info', message: `Restored v${version} as v${next.version}.` });
      this.detailChange.emit(next);
    } catch (err) {
      this.toasts.error(err);
    }
  }
}
