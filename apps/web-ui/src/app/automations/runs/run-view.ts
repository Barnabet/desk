import { ChangeDetectionStrategy, Component, computed, inject, input, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDetail, RunDetail } from '@desk/protocol';
import { agentActivity, dayTime, focusStep, href, runGraph, runStatusText, runTook, triggerText, type GraphSelection } from '@desk/ui-core';
import { Button } from '../../components/button';
import { ConfirmDialog } from '../../components/confirm-dialog';
import { EmptyState } from '../../components/empty-state';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';
import type { SessionState } from '../../core/session.service';
import { injectRun } from '../data';
import { GraphCanvas } from '../design/graph-canvas';
import { StepPanel } from './step-panel';

/** The run itself: its inputs, summary or reason. */
@Component({
  selector: 'aside[deskRunSide]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-panel', '[attr.aria-label]': "'Run #' + run().number" },
  template: `
    <p class="eyebrow">Run</p>
    <h2>{{ run().automation_title }}</h2>
    @if (run().summary) {
      <p>{{ run().summary }}</p>
    }
    @if (run().reason) {
      <p class="auto-issues">{{ run().reason }}</p>
    }
    <h3 class="auto-sub">Inputs</h3>
    @if (inputs().length) {
      <dl class="auto-facts">
        @for (i of inputs(); track i.key) {
          <div><dt class="mono">{{ i.key }}</dt><dd>{{ i.value }}</dd></div>
        }
      </dl>
    } @else {
      <p class="muted small">None.</p>
    }
  `,
})
export class RunSide {
  readonly run = input.required<RunDetail>();
  protected readonly inputs = computed(() => Object.entries(this.run().inputs).map(([key, v]) => ({ key, value: String(v) })));
}

/** One run (spec §8.3, view A): the graph lit with each step's state, the header's Open folder and Cancel run, and a side panel. */
@Component({
  selector: 'div[deskRunView]',
  imports: [Button, ConfirmDialog, EmptyState, GraphCanvas, RunSide, StepPanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (live.status() === 'missing') {
      <div deskEmptyState title="This run is gone" body="Desk keeps each automation's last 20 runs or 30 days of runs."><a [href]="back()">All runs</a></div>
    } @else {
      @if (run(); as run) {
        <div class="auto-run">
          <div class="auto-run-main">
            <header class="auto-run-head">
              <a class="muted small" [href]="back()">Runs ›</a>
              <h2>{{ 'Run #' + run.number }}</h2>
              <span class="muted small">{{ meta() }}</span>
              <span class="auto-last" [class]="'tone-' + status().tone"><span class="dot" aria-hidden="true"></span>{{ status().text }}</span>
              @if (run.version !== detail().version) {
                <span class="auto-badge">{{ 'v' + run.version }}</span>
              }
              <span class="grow"></span>
              <button deskButton size="sm" (click)="reveal()">Open folder</button>
              @if (going()) {
                <button deskButton size="sm" variant="danger" (click)="cancelling.set(true)">Cancel run…</button>
              }
            </header>
            <div deskGraphCanvas [def]="run.definition" [layout]="detail().layout" [startLabel]="trigger()" [selection]="sel()" [run]="graph() ?? undefined" [editable]="false" (pick)="pick($event)"></div>
          </div>
          @for (id of panelKey(); track id) {
            <aside deskStepPanel [projectId]="projectId()" [s]="s()" [run]="run" [stepId]="id" [grantsSuspended]="detail().grants_suspended"></aside>
          } @empty {
            <aside deskRunSide [run]="run"></aside>
          }
          @if (cancelling()) {
            <div deskConfirmDialog [title]="'Cancel run #' + run.number + '?'" confirmLabel="Cancel run" [danger]="true" (confirm)="cancel()" (cancel)="cancelling.set(false)">Its running steps stop: agents are stopped and scripts are killed. Steps that finished keep their results.</div>
          }
        </div>
      } @else {
        <p class="muted auto-loading">Loading…</p>
      }
    }
  `,
})
export class RunView {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly detail = input.required<AutomationDetail>();
  readonly runId = input.required<string>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly now = inject(NowService).now;
  protected readonly live = injectRun(() => this.s(), () => this.runId());
  protected readonly run = computed(() => this.live.value());
  protected readonly picked = signal<GraphSelection | null>(null);
  protected readonly cancelling = signal(false);
  private readonly activity = computed(() => {
    const out: Record<string, string | null> = {};
    const events = this.s().events;
    for (const st of this.run()?.steps ?? []) if (st.agent_id && st.status === 'running') out[st.step_id] = agentActivity(events, st.agent_id);
    return out;
  });
  protected readonly graph = computed(() => {
    const run = this.run();
    return run ? runGraph(run, this.now(), this.activity()) : null;
  });
  protected readonly sel = computed((): GraphSelection => {
    const picked = this.picked();
    if (picked) return picked;
    const run = this.run();
    const focus = run ? focusStep(run) : null;
    return focus ? { kind: 'step', id: focus } : { kind: 'start' };
  });
  protected readonly panelKey = computed(() => {
    const s = this.sel();
    return s.kind === 'step' ? [s.id] : [];
  });
  protected readonly status = computed(() => {
    const run = this.run();
    return run ? runStatusText(run) : { text: '', tone: 'idle' as const };
  });
  protected readonly trigger = computed(() => {
    const run = this.run();
    return run ? triggerText(run) : '';
  });
  protected readonly meta = computed(() => {
    const run = this.run();
    return run ? `${triggerText(run)} · ${dayTime(run.started_at, this.now())} · ${runTook(run, this.now())}` : '';
  });
  protected readonly going = computed(() => {
    const status = this.run()?.status;
    return status === 'running' || status === 'waiting';
  });
  protected readonly back = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations', automationId: this.detail().id, view: 'runs' }));

  protected pick(x: GraphSelection): void {
    this.picked.set(x.kind === 'none' ? { kind: 'start' } : x);
  }

  protected reveal(): void {
    const run = this.run();
    if (run) void this.bridge.call('app.revealPath', { runId: run.id }).catch((err: unknown) => this.toasts.error(err));
  }

  protected async cancel(): Promise<void> {
    this.cancelling.set(false);
    const run = this.run();
    if (!run) return;
    try {
      await this.bridge.call('automations.cancelRun', { runId: run.id });
    } catch (err) {
      this.toasts.error(err);
    }
  }
}
