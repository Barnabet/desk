import { ChangeDetectionStrategy, Component, computed, DestroyRef, effect, inject, signal, untracked, ViewEncapsulation } from '@angular/core';
import type { ChannelOutput } from '@desk/bff/contract';
import type { RuntimesReport, UsageResponse } from '@desk/protocol';
import { bytes, clock, duration, href, plural } from '@desk/ui-core';
import { Button } from '../components/button';
import { ConfirmDialog } from '../components/confirm-dialog';
import { EndpointPanel } from '../components/endpoint-panel';
import { describeError, ToastService } from '../components/toast';
import { singleFlight } from '../core/refresh';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { WebNotifications } from '../core/notifications';
import { tokens } from '../threads/tabs/usage-tab';
import { ModelsEditor } from './models-editor';

type DaemonStatusView = ChannelOutput<'daemon.status'>;
type AppInfo = ChannelOutput<'app.info'>;
type Period = 'all' | '30d' | '7d';
type Tally = [key: string, sum: { prompt: number; completion: number }];

const PERIOD_DAYS: Record<Exclude<Period, 'all'>, number> = { '30d': 30, '7d': 7 };
const PERIODS: Period[] = ['7d', '30d', 'all'];
const PERIOD_LABEL: Record<Period, string> = { '7d': '7 days', '30d': '30 days', all: 'All time' };

function proxyText(s: DaemonStatusView): string {
  return s.proxy === 'up' ? 'Reachable' : s.proxy === 'down' ? 'Unreachable. Threads pause and resume when it is back.' : 'Unknown';
}

/** desk web runs the Desk app's deskd through its LaunchAgent when that is installed for desk web's data dir, else this repository's (spec §3). */
function modeText(s: DaemonStatusView): string {
  if (s.mode === 'packaged') return `Bundled deskd ${s.bundledVersion}`;
  if (s.mode === 'web' && s.agent === 'installed') return 'The Desk app’s deskd, through its LaunchAgent';
  return 'Development (runs from this repository)';
}

function loginText(s: DaemonStatusView): string {
  if (s.agent === 'installed') return 'Yes, as a LaunchAgent';
  if (s.agent === 'missing') return 'No. Install the LaunchAgent to keep Desk running.';
  return s.mode === 'web' ? 'No. desk web starts it from this repository.' : 'Not on this platform';
}

/** Prompt and completion tokens per model or per project, the biggest first. */
function tally(rows: UsageResponse['rows'], key: 'model' | 'project_id'): Tally[] {
  const by = new Map<string, { prompt: number; completion: number }>();
  for (const r of rows) {
    const k = r[key];
    const v = by.get(k) ?? { prompt: 0, completion: 0 };
    v.prompt += r.prompt_tokens;
    v.completion += r.completion_tokens;
    by.set(k, v);
  }
  return [...by].sort((a, b) => b[1].prompt + b[1].completion - (a[1].prompt + a[1].completion));
}

/** deskd: running or not, the proxy, how it runs, and Start, or Restart and Stop. desk web offers no LaunchAgent repair. */
@Component({
  selector: 'section[deskDaemonSection]',
  imports: [Button, ConfirmDialog],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card sys-section', 'aria-labelledby': 'sys-daemon' },
  template: `
    <h2 id="sys-daemon">deskd</h2>
    @if (error(); as message) {
      <p class="field-error">{{ message }}</p>
    }
    @if (status(); as s) {
      <p class="status-line"><span class="dot" [class]="tone(s)" aria-hidden="true"></span><strong>{{ s.running ? 'Running' : 'Not running' }}</strong>@if (s.running) {<span class="muted">· v{{ s.version }} · pid {{ s.pid }}{{ uptime(s) }}</span>}</p>
      <dl class="sys-facts">
        <div>
          <dt>Model proxy</dt>
          <dd>{{ proxy(s) }}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{{ mode(s) }}</dd>
        </div>
        <div>
          <dt>Starts at login</dt>
          <dd>{{ login(s) }}</dd>
        </div>
      </dl>
      <div class="actions">
        @if (s.running) {
          <button deskButton size="sm" [pending]="busy() === 'restart'" [disabled]="busy() !== null" (click)="act('restart')">Restart</button>
          <button deskButton size="sm" variant="ghost" [pending]="busy() === 'stop'" [disabled]="busy() !== null" (click)="confirmStop.set(true)">Stop</button>
        } @else {
          <button deskButton size="sm" variant="primary" [pending]="busy() === 'start'" [disabled]="busy() !== null" (click)="act('start')">Start</button>
        }
      </div>
    } @else if (!error()) {
      <p class="muted">Checking…</p>
    }
    @if (confirmStop()) {
      <div deskConfirmDialog title="Stop deskd?" confirmLabel="Stop" danger (cancel)="confirmStop.set(false)" (confirm)="act('stop')">Running threads pause. They resume where they were when deskd starts again.</div>
    }
  `,
})
export class DaemonSection {
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly status = signal<DaemonStatusView | null>(null);
  protected readonly busy = signal<'start' | 'restart' | 'stop' | null>(null);
  protected readonly confirmStop = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly proxy = proxyText;
  protected readonly mode = modeText;
  protected readonly login = loginText;
  /** Counts the answers Start, Restart and Stop gave, so a status read that started before one cannot undo it. */
  private acted = 0;
  /** The 10-second poll: a slow deskd never piles up status reads, and nothing loads once the section is gone. */
  private readonly refresh = singleFlight(() => this.load());

  constructor() {
    void this.refresh.run();
    const poll = setInterval(() => void this.refresh.run(), 10_000);
    inject(DestroyRef).onDestroy(() => {
      clearInterval(poll);
      this.refresh.stop();
    });
  }

  protected tone(s: DaemonStatusView): string {
    return s.running ? (s.proxy === 'down' ? 'warn' : 'ok') : 'bad';
  }

  protected uptime(s: DaemonStatusView): string {
    return s.uptime_s !== null ? ` · up ${duration(s.uptime_s * 1000)}` : '';
  }

  protected async act(what: 'start' | 'restart' | 'stop'): Promise<void> {
    this.confirmStop.set(false);
    this.busy.set(what);
    try {
      // Literal operation names: the parity guard (parity.spec.ts) reads them.
      const next = what === 'start' ? await this.bridge.call('daemon.start', {}) : what === 'restart' ? await this.bridge.call('daemon.restart', {}) : await this.bridge.call('daemon.stop', {});
      this.acted++;
      this.status.set(next);
    } catch (err) {
      this.acted++;
      this.toasts.error(err);
      void this.refresh.run();
    } finally {
      this.busy.set(null);
    }
  }

  private load(): Promise<void> {
    const acted = this.acted;
    return this.bridge.call('daemon.status', {}).then(
      (v) => {
        if (acted !== this.acted) return;
        this.status.set(v);
        this.error.set(null);
      },
      (err: unknown) => {
        if (acted === this.acted) this.error.set(describeError(err).message);
      },
    );
  }
}

/** Tokens by model and by project over 7 days, 30 days or all time. */
@Component({
  selector: 'section[deskUsageSection]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card sys-section', 'aria-labelledby': 'sys-usage' },
  template: `
    <div class="sys-head">
      <h2 id="sys-usage">Usage</h2>
      <div class="segmented" role="group" aria-label="Period">
        @for (p of periods; track p) {
          <button type="button" [attr.aria-pressed]="period() === p" (click)="period.set(p)">{{ periodLabel[p] }}</button>
        }
      </div>
    </div>
    @if (error(); as message) {
      <p class="field-error">{{ message }}</p>
    }
    @if (usage(); as u) {
      @if (u.rows.length) {
        <p class="small">{{ tokens(u.totals.prompt_tokens) }} prompt and {{ tokens(u.totals.completion_tokens) }} completion tokens.</p>
        <div class="usage-grid">
          <table class="usage-table">
            <caption>By model</caption>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">Prompt</th>
                <th scope="col">Completion</th>
              </tr>
            </thead>
            <tbody>
              @for (row of byModel(); track row[0]) {
                <tr>
                  <td><span class="mono">{{ row[0] }}</span></td>
                  <td [title]="row[1].prompt.toLocaleString()">{{ tokens(row[1].prompt) }}</td>
                  <td [title]="row[1].completion.toLocaleString()">{{ tokens(row[1].completion) }}</td>
                </tr>
              }
            </tbody>
          </table>
          <table class="usage-table">
            <caption>By project</caption>
            <thead>
              <tr>
                <th scope="col">Project</th>
                <th scope="col">Prompt</th>
                <th scope="col">Completion</th>
              </tr>
            </thead>
            <tbody>
              @for (row of byProject(); track row[0]) {
                <tr>
                  <td>@if (names().has(row[0])) {<a [href]="projectHref(row[0])">{{ names().get(row[0]) }}</a>} @else {<span class="muted">{{ row[0] === '_global' ? 'Outside projects' : 'Archived project' }}</span>}</td>
                  <td [title]="row[1].prompt.toLocaleString()">{{ tokens(row[1].prompt) }}</td>
                  <td [title]="row[1].completion.toLocaleString()">{{ tokens(row[1].completion) }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else {
        <p class="muted">No model calls in this period.</p>
      }
    } @else if (!error()) {
      <p class="muted">Loading…</p>
    }
  `,
})
export class UsageSection {
  private readonly bridge = inject(DeskBridge);
  private readonly global = inject(GlobalStore).state;
  protected readonly periods = PERIODS;
  protected readonly periodLabel = PERIOD_LABEL;
  protected readonly tokens = tokens;
  protected readonly period = signal<Period>('30d');
  protected readonly usage = signal<UsageResponse | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly names = computed(() => new Map(this.global().overview.map((p) => [p.project.id, p.project.name])));
  protected readonly byModel = computed(() => tally(this.usage()?.rows ?? [], 'model'));
  protected readonly byProject = computed(() => tally(this.usage()?.rows ?? [], 'project_id'));

  constructor() {
    effect((onCleanup) => {
      const period = this.period();
      let live = true;
      onCleanup(() => (live = false));
      const since = period === 'all' ? undefined : new Date(Date.now() - PERIOD_DAYS[period] * 86_400_000).toISOString().slice(0, 10);
      untracked(() => this.bridge.call('usage', since ? { since } : {})).then(
        (u) => {
          if (!live) return;
          this.usage.set(u);
          this.error.set(null);
        },
        (err: unknown) => {
          if (live) this.error.set(describeError(err).message);
        },
      );
    });
  }

  protected projectHref(id: string): string {
    return href({ name: 'project', id, tab: 'conversation' });
  }
}

/** The last 50 system notices (proxy outages, restarts, recoveries), newest first. */
@Component({
  selector: 'section[deskNoticesSection]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card sys-section', 'aria-labelledby': 'sys-notices' },
  template: `
    <h2 id="sys-notices">System notices</h2>
    @if (recent().length) {
      <ol class="notices" reversed>
        @for (n of recent(); track n.eventId) {
          <li class="notice" [class]="'notice-' + n.level"><span class="mono small muted">{{ clock(n.ts) }}</span><span class="grow">{{ n.message }}</span><span class="small muted">{{ names().get(n.projectId) ?? '' }}</span></li>
        }
      </ol>
    } @else {
      <p class="muted">Nothing to report since desk web started. Proxy outages, restarts and recoveries show up here.</p>
    }
  `,
})
export class NoticesSection {
  private readonly global = inject(GlobalStore).state;
  protected readonly clock = clock;
  protected readonly recent = computed(() => this.global().system.notices.slice().reverse().slice(0, 50));
  protected readonly names = computed(() => new Map(this.global().overview.map((p) => [p.project.id, p.project.name])));
}

/**
 * "From the app" is desk web's switch (web-settings.json): browser notifications, which also need this browser's
 * permission, asked inside the click that turns them on. "From deskd" is deskd's own, used while no Desk tab can notify.
 */
@Component({
  selector: 'section[deskNotificationsSection]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card sys-section', 'aria-labelledby': 'sys-notify' },
  template: `
    <h2 id="sys-notify">Notifications</h2>
    <label class="toggle">
      <input type="checkbox" [checked]="appOn() ?? false" [disabled]="appOn() === null || appPending()" (change)="setApp($event)" />
      <span>
        <strong>From the app</strong>
        <span class="muted small">Approvals, questions, hand-offs and stuck threads, while Desk is open in this browser. Silent while its tab has focus.</span>
      </span>
    </label>
    @if (appOn() && permission() === 'default') {
      <p class="field-hint">This browser has not allowed notifications from Desk yet. <button deskButton size="sm" variant="ghost" (click)="allow()">Allow notifications</button></p>
    } @else if (appOn() && permission() === 'denied') {
      <p class="field-hint">This browser blocks notifications from Desk. Allow them in its site settings for this address, then come back to this tab.</p>
    }
    <label class="toggle">
      <input type="checkbox" [checked]="daemon() === 'auto'" [disabled]="daemon() === null || daemonPending()" (change)="setDaemon($event)" />
      <span>
        <strong>From deskd when the app is closed</strong>
        <span class="muted small">deskd stays quiet while a Desk tab can notify you, so you never get both.</span>
      </span>
    </label>
  `,
})
export class NotificationsSection {
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly notifications = inject(WebNotifications);
  protected readonly permission = this.notifications.permission;
  protected readonly appOn = signal<boolean | null>(null);
  protected readonly daemon = signal<'auto' | 'off' | null>(null);
  /**
   * Each switch takes no click while its write is pending (disabled, and its handler refuses a change that still arrives):
   * a later click's answer could otherwise land first and be overwritten by the earlier one's.
   */
  protected readonly appPending = signal(false);
  protected readonly daemonPending = signal(false);

  constructor() {
    this.bridge.call('app.settings', {}).then(
      (s) => this.appOn.set(s.notifications),
      () => this.appOn.set(null),
    );
    this.bridge.call('config.get', {}).then(
      (c) => this.daemon.set(c.notifications),
      () => this.daemon.set(null),
    );
  }

  protected async setApp(e: Event): Promise<void> {
    const box = e.target as HTMLInputElement;
    if (this.appPending()) {
      box.checked = this.appOn() ?? false;
      return;
    }
    const on = box.checked;
    // Browsers prompt only inside a user gesture, so the prompt opens here, before anything is awaited. The switch saves
    // without waiting on it: a prompt nobody answers must not hold the switch, and the hint below says the browser has not
    // allowed Desk yet.
    if (on && this.permission() !== 'granted') void this.notifications.request();
    this.appPending.set(true);
    try {
      this.appOn.set((await this.bridge.call('app.updateSettings', { notifications: on })).notifications);
    } catch (err) {
      this.toasts.error(err);
    } finally {
      // Angular writes [checked] only when appOn changes: the box shows what is stored, as React's controlled one does.
      box.checked = this.appOn() ?? false;
      this.appPending.set(false);
    }
  }

  protected allow(): void {
    void this.notifications.request();
  }

  protected async setDaemon(e: Event): Promise<void> {
    const box = e.target as HTMLInputElement;
    if (this.daemonPending()) {
      box.checked = this.daemon() === 'auto';
      return;
    }
    this.daemonPending.set(true);
    try {
      this.daemon.set((await this.bridge.call('config.patch', { notifications: box.checked ? 'auto' : 'off' })).notifications);
    } catch (err) {
      this.toasts.error(err);
    } finally {
      box.checked = this.daemon() === 'auto';
      this.daemonPending.set(false);
    }
  }
}

/** The data directory, the skill environments Desk set up (and removing unused ones), the app, and the logs. */
@Component({
  selector: 'section[deskAboutSection]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card sys-section', 'aria-labelledby': 'sys-about' },
  template: `
    <h2 id="sys-about">Data</h2>
    <dl class="sys-facts">
      <div>
        <dt>Data directory</dt>
        <dd class="mono">{{ info()?.dataDir ?? '…' }}</dd>
      </div>
      @if (report()) {
        <div>
          <dt>Skill environments</dt>
          <dd>{{ envText() }}@if (orphans().length) {&ngsp;·&ngsp;<button deskButton size="sm" [pending]="pending()" (click)="cleanup()">Clean up unused ({{ size(orphanBytes()) }})</button>}</dd>
        </div>
      }
      <div>
        <dt>App</dt>
        <dd>{{ appLine() }}</dd>
      </div>
    </dl>
    <div class="actions">
      <button deskButton size="sm" (click)="revealLogs()">Reveal logs</button>
    </div>
  `,
})
export class AboutSection {
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly size = bytes;
  protected readonly info = signal<AppInfo | null>(null);
  protected readonly report = signal<RuntimesReport | null>(null);
  protected readonly pending = signal(false);
  protected readonly orphans = computed(() => this.report()?.envs.filter((e) => e.orphan) ?? []);
  protected readonly orphanBytes = computed(() => this.orphans().reduce((n, e) => n + e.bytes, 0));
  protected readonly envText = computed(() => {
    const r = this.report();
    return r?.envs.length ? `${bytes(r.bytes)} for ${plural(r.envs.length, 'skill')}` : 'None yet';
  });
  protected readonly appLine = computed(() => {
    const i = this.info();
    return `Desk ${i?.version ?? ''} ${i && !i.packaged ? '(development)' : ''}`;
  });

  constructor() {
    this.bridge.call('app.info', {}).then(
      (i) => this.info.set(i),
      () => this.info.set(null),
    );
    void this.loadRuntimes();
  }

  protected async cleanup(): Promise<void> {
    this.pending.set(true);
    try {
      const r = await this.bridge.call('system.runtimesCleanup', {});
      this.toasts.toast({ tone: 'info', message: `Removed ${plural(r.removed, 'unused environment')} (${bytes(r.bytes)}).` });
      await this.loadRuntimes();
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.pending.set(false);
    }
  }

  protected revealLogs(): void {
    this.bridge.call('app.revealLogs', {}).catch((err: unknown) => this.toasts.error(err));
  }

  private loadRuntimes(): Promise<void> {
    return this.bridge.call('system.runtimes', {}).then(
      (r) => this.report.set(r),
      () => this.report.set(null),
    );
  }
}

/**
 * The desktop's Appearance switch (System, Light, Dark; master's dark mode) maps to Electron's nativeTheme. A browser page
 * follows the system's prefers-color-scheme instead, and desk web answers `app.settings` with `appearance: 'system'` and
 * `not_offered` for Light or Dark. So the web shows the same choice with System on and all three disabled, and says why.
 */
@Component({
  selector: 'section[deskAppearanceSection]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card sys-section', 'aria-labelledby': 'sys-appearance' },
  template: `
    <h2 id="sys-appearance">Appearance</h2>
    <div class="segmented" role="group" aria-label="Appearance">
      <button type="button" aria-pressed="true" disabled>System</button>
      <button type="button" aria-pressed="false" disabled>Light</button>
      <button type="button" aria-pressed="false" disabled>Dark</button>
    </div>
    <p class="field-hint">The web UI follows your system's light or dark setting. Choosing one is only offered in the Desk app.</p>
  `,
})
export class AppearanceSection {}

/** The machine room: deskd, the model endpoint and registry, usage, notices, appearance, notifications and data (SystemScreen.tsx). */
@Component({
  selector: 'div[deskSystemScreen]',
  imports: [AboutSection, AppearanceSection, DaemonSection, EndpointPanel, ModelsEditor, NoticesSection, NotificationsSection, UsageSection],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'page system' },
  template: `
    <h1 class="title">System</h1>
    <div class="sys-grid">
      <section deskDaemonSection></section>
      <section class="card sys-section" aria-labelledby="sys-endpoint">
        <h2 id="sys-endpoint">Model endpoint</h2>
        <div deskEndpointPanel></div>
      </section>
      <section deskAppearanceSection></section>
      <section deskNotificationsSection></section>
      <section deskAboutSection></section>
    </div>
    <section class="card sys-section" aria-labelledby="sys-models">
      <h2 id="sys-models">Model registry</h2>
      <p class="field-hint">The models projects can choose. Concurrency caps how many calls to a model run at once across all projects.</p>
      <div deskModelsEditor></div>
    </section>
    <section deskUsageSection></section>
    <section deskNoticesSection></section>
  `,
})
export class SystemScreen {}
