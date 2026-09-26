import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, output, signal } from '@angular/core';
import { runtimeKey } from '@desk/bff/contract';
import type { CatalogEntry, CatalogInstall } from '@desk/protocol';
import { Button } from '../../components/button';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { GlobalStore } from '../../core/global.store';
import { scopeArg } from '../data';
import { installRef, runtimeWords } from './data';

/**
 * An installed catalog skill's environment: Ready, Setting up… (with live progress), or Failed with Retry (RuntimeLine.tsx).
 * The React component switches roots (nothing, a `p`, a `div`), so this host is `display: contents`.
 */
@Component({
  selector: 'div[deskRuntimeLine]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @switch (install().runtime) {
      @case ('ready') {
        <p class="runtime-line ready" role="status"><span class="runtime-dot" aria-hidden="true"></span>Ready · {{ words() }}</p>
      }
      @case ('preparing') {
        <p class="runtime-line preparing" role="status"><span class="runtime-dot" aria-hidden="true"></span>Setting up…{{ progressText() }}</p>
      }
      @case ('failed') {
        <div class="runtime-line failed" role="alert">
          <span class="runtime-dot" aria-hidden="true"></span>
          <span class="grow">Setup failed{{ reasonText() }} Agents can't run this skill's scripts until it's set up.</span>
          <button deskButton size="sm" [pending]="pending()" (click)="retry()">Retry</button>
        </div>
      }
    }
  `,
})
export class RuntimeLine {
  readonly entry = input.required<Pick<CatalogEntry, 'id' | 'runtime'>>();
  readonly install = input.required<CatalogInstall>();
  /** The retry was accepted (React's `onRetried`). */
  readonly retried = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly global = inject(GlobalStore);
  private readonly toasts = inject(ToastService);
  protected readonly pending = signal(false);

  protected readonly words = computed(() => runtimeWords(this.entry()));
  /** This skill's live setup step; an own key only, since the skill's name comes from the catalog. */
  private readonly progress = computed(() => {
    const i = this.install();
    const all = this.global.state().runtimes.progress;
    const key = runtimeKey(i.scope, i.project_id, this.entry().id);
    return Object.hasOwn(all, key) ? all[key] : undefined;
  });
  protected readonly progressText = computed(() => {
    const p = this.progress();
    return p ? ` ${p.step}${p.total ? ` (${(p.done ?? 0) + 1} of ${p.total})` : ''}` : '';
  });
  protected readonly reasonText = computed(() => {
    const reason = this.install().runtime_reason;
    return reason ? `: ${reason}` : '.';
  });

  protected async retry(): Promise<void> {
    const entry = this.entry();
    this.pending.set(true);
    try {
      await this.bridge.call('skills.runtimeRetry', { ...scopeArg(installRef(entry.id, this.install())), name: entry.id });
      this.toasts.toast({ tone: 'info', message: `Setting up ${entry.id} again.` });
      this.retried.emit();
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.pending.set(false);
    }
  }
}
