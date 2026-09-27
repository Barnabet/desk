import { ChangeDetectionStrategy, Component, computed, inject, input, signal, ViewEncapsulation } from '@angular/core';
import { AutomationExport, type AutomationDetail, type AutomationSummary } from '@desk/protocol';
import { dayTime, href, lastRunText, listNote, whenText, type Route } from '@desk/ui-core';
import { Button } from '../components/button';
import { EmptyState } from '../components/empty-state';
import { ToastService } from '../components/toast';
import { primeDraft } from '../conversation/draft';
import { DeskBridge, DeskCallError } from '../core/desk-bridge';
import { NowService } from '../core/now.service';
import { RouteService } from '../core/route.service';
import type { SessionState } from '../core/session.service';
import { injectAutomationList } from './data';
import { NameDialog } from './dialogs/name-dialog';
import { RunDialog } from './dialogs/run-dialog';
import { TurnOnDialog } from './dialogs/turn-on-dialog';

/** Mockup 1: every automation with its switch, when it runs, its last run and its next one. */
@Component({
  selector: 'div[deskAutomationList]',
  imports: [Button, EmptyState, NameDialog, RunDialog, TurnOnDialog],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-list' },
  template: `
    <header class="auto-list-head">
      <h1 class="title">Automations</h1>
      <span class="grow"></span>
      <button deskButton (click)="file.click()">Import…</button>
      <button deskButton (click)="naming.set({})">Blank automation</button>
      <button deskButton variant="primary" (click)="describe()">Describe one to Desk</button>
      <input #file type="file" accept=".json,application/json" hidden data-testid="automation-import" (change)="picked($event)" />
    </header>
    @if (live.status() === 'loading') {
      <p class="muted">Loading…</p>
    } @else if (live.status() === 'error' && !live.value()) {
      <div deskEmptyState title="Couldn't load automations" [body]="live.error()"></div>
    } @else if (rows().length) {
      <table class="auto-table">
        <thead>
          <tr>
            <th>Automation</th>
            <th>On</th>
            <th>When</th>
            <th>Last run</th>
            <th>Next</th>
          </tr>
        </thead>
        <tbody>
          @for (a of rows(); track a.id) {
            <tr>
              <td>
                <a class="auto-row-title" [href]="a.href">{{ a.summary.title }}</a>
                <div class="muted small">{{ a.note }}</div>
              </td>
              <td>
                <button type="button" role="switch" class="switch" [attr.aria-checked]="a.summary.enabled" [attr.aria-label]="a.switchLabel" [disabled]="busy() === a.id" (click)="toggle(a.summary)">
                  <span class="switch-knob"></span>
                </button>
              </td>
              <td>{{ a.when }}</td>
              <td>
                @if (a.last; as last) {
                  <span class="auto-last" [class]="'tone-' + last.tone"><span class="dot" aria-hidden="true"></span>{{ last.text }}</span>
                } @else {
                  <span class="muted">never run</span>
                }
              </td>
              <td>{{ a.next }}@if (!a.next) {<span class="muted">n/a</span>}</td>
            </tr>
          }
        </tbody>
      </table>
    } @else {
      <div deskEmptyState title="No automations yet" body="Describe something you do again and again, and Desk builds, tests and proposes an automation. Or start from a blank one."></div>
    }
    @if (naming(); as n) {
      <div
        deskNameDialog
        [title]="n.importing ? 'Import as…' : 'New automation'"
        [confirmLabel]="n.importing ? 'Import' : 'Create'"
        [initial]="n.importing ? n.importing.name + '-2' : ''"
        [hint]="n.importing ? 'An automation is already called ' + n.importing.name + '. Pick another name for this one.' : undefined"
        [taken]="taken()"
        (close)="naming.set(null)"
        (confirm)="named($event)"
      ></div>
    }
    @if (turnOn(); as d) {
      <div deskTurnOnDialog [detail]="d" (close)="turnOn.set(null)" (done)="turnedOn()" (testFirst)="testFirst(d)"></div>
    }
    @if (testing(); as d) {
      <div deskRunDialog [target]="d" [test]="true" (close)="testing.set(null)" (started)="started(d.id, $event)"></div>
    }
  `,
})
export class AutomationList {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  private readonly now = inject(NowService).now;
  protected readonly live = injectAutomationList(() => this.projectId(), () => this.s());
  protected readonly naming = signal<null | { importing?: AutomationExport }>(null);
  protected readonly turnOn = signal<AutomationDetail | null>(null);
  protected readonly testing = signal<AutomationDetail | null>(null);
  protected readonly busy = signal<string | null>(null);
  protected readonly taken = computed(() => (this.live.value() ?? []).map((a) => a.name));
  protected readonly rows = computed(() => {
    const now = this.now();
    return (this.live.value() ?? []).map((a) => ({
      id: a.id,
      summary: a,
      href: href(this.at(a.id)),
      note: listNote(a),
      switchLabel: a.enabled ? `Turn off ${a.title}` : `Turn on ${a.title}`,
      when: whenText(a.schedules),
      last: a.last_run ? lastRunText(a.last_run, now) : null,
      next: a.next_due ? dayTime(a.next_due, now) : null,
    }));
  });

  private at(automationId: string, extra: { view: 'design' } | { view: 'runs'; runId: string } = { view: 'design' }): Route {
    return { name: 'project', id: this.projectId(), tab: 'automations', automationId, ...extra };
  }

  protected describe(): void {
    primeDraft(this.projectId(), "I'd like to automate: ");
    this.routes.navigate({ name: 'project', id: this.projectId(), tab: 'conversation' });
  }

  protected picked(e: Event): void {
    const el = e.target as HTMLInputElement;
    const file = el.files?.[0];
    el.value = '';
    if (file) void this.readImport(file);
  }

  private async readImport(file: File): Promise<void> {
    let raw: unknown = null;
    try {
      raw = JSON.parse(await file.text());
    } catch {
      raw = null;
    }
    const parsed = AutomationExport.safeParse(raw);
    if (!parsed.success) {
      this.toasts.toast({ tone: 'error', message: "That file isn't a Desk automation export." });
      return;
    }
    await this.doImport(parsed.data);
  }

  private async doImport(exp: AutomationExport): Promise<void> {
    try {
      const r = await this.bridge.call('automations.import', { projectId: this.projectId(), exp });
      this.toasts.toast({ tone: 'info', message: `Imported ${r.automation.title}. It stays off until you turn it on.` });
      this.routes.navigate(this.at(r.automation.id));
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 409) this.naming.set({ importing: exp });
      else this.toasts.error(err);
    }
  }

  protected named(name: string): void {
    const importing = this.naming()?.importing;
    this.naming.set(null);
    if (importing) void this.doImport({ ...importing, name });
    else this.routes.navigate({ name: 'project', id: this.projectId(), tab: 'automations', draft: name });
  }

  protected async toggle(a: AutomationSummary): Promise<void> {
    this.busy.set(a.id);
    try {
      if (a.enabled) {
        await this.bridge.call('automations.setEnabled', { id: a.id, enabled: false });
        this.live.reload();
      } else this.turnOn.set(await this.bridge.call('automations.get', { id: a.id }));
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(null);
    }
  }

  protected turnedOn(): void {
    this.turnOn.set(null);
    this.live.reload();
  }

  protected testFirst(d: AutomationDetail): void {
    this.testing.set(d);
    this.turnOn.set(null);
  }

  protected started(automationId: string, runId: string): void {
    this.routes.navigate(this.at(automationId, { view: 'runs', runId }));
  }
}
