import { ChangeDetectionStrategy, Component, computed, inject, input, output, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDetail } from '@desk/protocol';
import { href, versionBadge, type AutomationView, type Route } from '@desk/ui-core';
import { Button } from '../components/button';
import { ConfirmDialog } from '../components/confirm-dialog';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { RouteService } from '../core/route.service';
import { RunDialog } from './dialogs/run-dialog';
import { TurnOnDialog } from './dialogs/turn-on-dialog';

type Dialog = null | 'run' | 'test' | 'turn-on' | 'delete';

/** One automation's header (mockup 2): title, version badge, the switch, Test… and Run now…, and the sub-tabs. */
@Component({
  selector: 'header[deskAutomationHeader]',
  imports: [Button, ConfirmDialog, RunDialog, TurnOnDialog],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'automation-head' },
  template: `
    <div class="automation-title-row">
      <a class="muted small" [href]="all()">Automations ›</a>
      <h1 class="automation-title">{{ detail().title }}</h1>
      <span class="auto-badge" [class.ok]="detail().tested_version === detail().version">{{ badge() }}</span>
      @if (detail().grants_suspended) {
        <a class="auto-badge warn" [href]="grantsHref()">grants suspended</a>
      }
      <span class="grow"></span>
      <span class="muted small">{{ detail().enabled ? 'On' : 'Off' }}</span>
      <button type="button" role="switch" class="switch" [attr.aria-checked]="detail().enabled" [attr.aria-label]="switchLabel()" [disabled]="switching()" (click)="toggle()">
        <span class="switch-knob"></span>
      </button>
      <button deskButton (click)="dialog.set('test')">Test…</button>
      <button deskButton variant="primary" (click)="dialog.set('run')">Run now…</button>
    </div>
    <nav class="automation-tabs" aria-label="Automation">
      @for (t of tabs(); track t.view) {
        <a [href]="t.href" [attr.aria-current]="view() === t.view ? 'page' : null">{{ t.label }}</a>
      }
      <span class="grow"></span>
      <button deskButton size="sm" variant="ghost" (click)="exportIt()">Export…</button>
      <button deskButton size="sm" variant="ghost" (click)="dialog.set('delete')">Delete…</button>
    </nav>
    @if (dialog() === 'run' || dialog() === 'test') {
      <div deskRunDialog [target]="detail()" [test]="dialog() === 'test'" (close)="dialog.set(null)" (started)="started($event)"></div>
    }
    @if (dialog() === 'turn-on') {
      <div deskTurnOnDialog [detail]="detail()" (close)="dialog.set(null)" (done)="turnedOn($event)" (testFirst)="dialog.set('test')"></div>
    }
    @if (dialog() === 'delete') {
      <div deskConfirmDialog [title]="'Delete ' + detail().title + '?'" confirmLabel="Delete" [danger]="true" (confirm)="remove()" (cancel)="dialog.set(null)">Its runs are cancelled and its schedules stop. Its history, and the files its runs put in the Library, are kept.</div>
    }
  `,
})
export class AutomationHeader {
  readonly projectId = input.required<string>();
  readonly detail = input.required<AutomationDetail>();
  readonly view = input.required<AutomationView>();
  /** React's onChange. */
  readonly detailChange = output<AutomationDetail>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  protected readonly dialog = signal<Dialog>(null);
  protected readonly switching = signal(false);
  protected readonly all = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations' }));
  protected readonly badge = computed(() => versionBadge(this.detail()));
  protected readonly switchLabel = computed(() => (this.detail().enabled ? `Turn off ${this.detail().title}` : `Turn on ${this.detail().title}`));
  protected readonly grantsHref = computed(() => href(this.route('grants')));
  protected readonly tabs = computed(() => {
    const d = this.detail();
    const runs = d.last_run?.number ?? 0;
    const tab = (view: AutomationView, label: string) => ({ view, label, href: href(this.route(view)) });
    return [tab('design', 'Design'), tab('runs', runs ? `Runs (${runs})` : 'Runs'), tab('versions', 'Versions'), tab('grants', d.grants.length ? `Grants (${d.grants.length})` : 'Grants')];
  });

  private route(view: AutomationView, runId?: string): Route {
    return { name: 'project', id: this.projectId(), tab: 'automations', automationId: this.detail().id, view, ...(runId ? { runId } : {}) };
  }

  protected async toggle(): Promise<void> {
    const d = this.detail();
    if (!d.enabled) {
      this.dialog.set('turn-on');
      return;
    }
    this.switching.set(true);
    try {
      this.detailChange.emit(await this.bridge.call('automations.setEnabled', { id: d.id, enabled: false }));
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.switching.set(false);
    }
  }

  protected async exportIt(): Promise<void> {
    const d = this.detail();
    try {
      const exp = await this.bridge.call('automations.export', { id: d.id });
      await this.bridge.call('app.saveFile', { name: `${d.name}.desk-automation.json`, data: new TextEncoder().encode(`${JSON.stringify(exp, null, 2)}\n`) });
    } catch (err) {
      this.toasts.error(err);
    }
  }

  protected async remove(): Promise<void> {
    this.dialog.set(null);
    const d = this.detail();
    try {
      await this.bridge.call('automations.remove', { id: d.id });
      this.toasts.toast({ tone: 'info', message: `Deleted ${d.title}.` });
      this.routes.navigate({ name: 'project', id: this.projectId(), tab: 'automations' });
    } catch (err) {
      this.toasts.error(err);
    }
  }

  protected started(runId: string): void {
    this.dialog.set(null);
    this.routes.navigate(this.route('runs', runId));
  }

  protected turnedOn(next: AutomationDetail): void {
    this.dialog.set(null);
    this.detailChange.emit(next);
  }
}

/** The header of a Blank automation before its first save. */
@Component({
  selector: 'header[deskDraftHeader]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'automation-head' },
  template: `
    <div class="automation-title-row">
      <a class="muted small" [href]="all()">Automations ›</a>
      <h1 class="automation-title">New automation</h1>
      <span class="auto-badge mono">{{ name() }}</span>
      <span class="muted small">Not saved yet: add a step, then Save.</span>
    </div>
    <nav class="automation-tabs" aria-label="Automation">
      <a [href]="self()" aria-current="page">Design</a>
    </nav>
  `,
})
export class DraftHeader {
  readonly projectId = input.required<string>();
  readonly name = input.required<string>();
  protected readonly all = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations' }));
  protected readonly self = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations', draft: this.name() }));
}
