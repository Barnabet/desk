import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, ViewEncapsulation, afterNextRender, afterRenderEffect, computed, effect, inject, input, output, signal, viewChild } from '@angular/core';
import type { ProjectState, ServiceRow } from '@desk/client';
import { ago, duration } from '@desk/ui-core';
import { Button } from '../components/button';
import { Sheet } from '../components/sheet';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { NowService } from '../core/now.service';

const STOP_REASON: Record<string, string> = {
  requested: 'stopped',
  restart: 'restarting',
  thread_archived: 'stopped · thread archived',
  project_archived: 'stopped · project archived',
  daemon_shutdown: 'stopped · deskd restarted',
  daemon_restart: 'stopped · deskd restarted',
};

/** Only loopback http(s) URLs are ever offered to open (they come from a process's own output). */
export function openableUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:') && ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname) ? u.toString() : null;
  } catch {
    return null;
  }
}

export function serviceState(s: ServiceRow, now: number): { tone: 'run' | 'fail' | 'off'; text: string } {
  if (s.status === 'running') return { tone: 'run', text: `running · ${duration(now - Date.parse(s.started_at))}` };
  const when = s.ended_at ? ` · ${ago(s.ended_at, now) === 'now' ? 'just now' : `${ago(s.ended_at, now)} ago`}` : '';
  if (s.status === 'exited') {
    const failed = s.exit_signal !== null || s.exit_code !== 0;
    return { tone: failed ? 'fail' : 'off', text: `exited (${s.exit_signal ?? s.exit_code})${when}` };
  }
  return { tone: 'off', text: `${STOP_REASON[s.stop_reason ?? 'requested'] ?? 'stopped'}${when}` };
}

const port = (url: string | null) => {
  if (!url) return null;
  try {
    const u = new URL(url);
    return `:${u.port || (u.protocol === 'https:' ? '443' : '80')}`;
  } catch {
    return null;
  }
};

/** A service's log tail, refreshed every second while open. Plain text: it is the process's own output. */
@Component({
  selector: 'div[deskLogsSheet]',
  imports: [Sheet, Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="service().name + ' · logs'" [width]="860" (close)="close.emit()">
      <p class="small muted service-command"><code>{{ service().command }}</code>{{ service().cwd !== '.' ? ' in ' + service().cwd : '' }}</p>
      @if (error()) {
        <p class="field-error">{{ error() }}</p>
      }
      <pre class="service-log" #pre [attr.aria-label]="service().name + ' log'">{{ text() === null ? 'Loading…' : text() || '(no output yet)' }}</pre>
      <div class="sheet-footer"><button deskButton (click)="close.emit()">Close</button></div>
    </div>
  `,
})
export class LogsSheet {
  readonly service = input.required<ServiceRow>();
  readonly close = output<void>();
  protected readonly text = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);
  private readonly pre = viewChild.required<ElementRef<HTMLPreElement>>('pre');
  private readonly serviceId = computed(() => this.service().id);
  /** Whether the log is at its bottom, so a new tail scrolls it down. Read only after a render: never rendered. */
  private pinned = true;

  constructor() {
    const bridge = inject(DeskBridge);
    effect((onCleanup) => {
      const id = this.serviceId();
      let stop = false;
      const load = async () => {
        try {
          const r = await bridge.call('services.logs', { id, lines: 500 });
          if (!stop) {
            this.text.set(r.text);
            this.error.set(null);
          }
        } catch (err) {
          if (!stop) this.error.set(err instanceof Error ? err.message : String(err));
        }
      };
      void load();
      const timer = setInterval(() => void load(), 1000);
      onCleanup(() => {
        stop = true;
        clearInterval(timer);
      });
    });
    afterRenderEffect(() => {
      this.text();
      const el = this.pre().nativeElement;
      if (this.pinned) el.scrollTop = el.scrollHeight;
    });
    // Not a template (scroll) listener: that would schedule change detection on every scroll event, for nothing.
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      const el = this.pre().nativeElement;
      const onScroll = () => {
        const pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        if (pinned !== this.pinned) this.pinned = pinned;
      };
      el.addEventListener('scroll', onScroll, { passive: true });
      destroyRef.onDestroy(() => el.removeEventListener('scroll', onScroll));
    });
  }
}

/** The project's services under the plan: status, URL, where they run, and Open / Logs / Restart / Stop / Start. */
@Component({
  selector: 'section[deskServicesCard]',
  imports: [Button, LogsSheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  // Without services the desktop renders nothing: the host drops its classes, hides and stays empty.
  host: { '[class]': "project().services.length ? 'card services-card' : null", 'aria-label': 'Services', '[hidden]': '!project().services.length' },
  template: `
    @if (project().services.length) {
      <div class="services-head">
        <h2>Services</h2>
        <span class="chip chip-idle">{{ running() ? running() + ' running' : 'none running' }}</span>
      </div>
      <ul class="services-list">
        @for (s of project().services; track s.id) {
          @let st = stateOf(s);
          @let open = s.status === 'running' ? openable(s.url) : null;
          <li class="service" [class]="'service-' + st.tone" [attr.aria-label]="s.name + ', ' + st.text">
            <span class="service-dot" aria-hidden="true"></span>
            <div class="service-text">
              <span class="service-name">{{ s.name }}@if (s.status === 'running' && portOf(s.url)) {<span class="service-port">{{ portOf(s.url) }}</span>}</span>
              <span class="service-sub" [title]="st.text + ' · ' + s.command">{{ st.text }} · from {{ origin(s) }}</span>
            </div>
            <div class="service-actions">
              @if (open) {
                <button deskButton size="sm" variant="ghost" [title]="open" (click)="openUrl(open)">Open</button>
              }
              <button deskButton size="sm" variant="ghost" (click)="logsFor.set(s)">Logs</button>
              @if (s.status === 'running') {
                <button deskButton size="sm" variant="ghost" [pending]="busy() === s.id + ':restart'" [disabled]="busy() !== null" (click)="act(s, 'restart')">Restart</button>
                <button deskButton size="sm" variant="ghost" [pending]="busy() === s.id + ':stop'" [disabled]="busy() !== null" (click)="act(s, 'stop')" [attr.aria-label]="'Stop ' + s.name">Stop</button>
              } @else {
                <button deskButton size="sm" [pending]="busy() === s.id + ':start'" [disabled]="busy() !== null" (click)="act(s, 'start')" [attr.aria-label]="'Start ' + s.name">Start</button>
              }
            </div>
          </li>
        }
      </ul>
      @if (logsService(); as service) {
        <div deskLogsSheet [service]="service" (close)="logsFor.set(null)"></div>
      }
    }
  `,
})
export class ServicesCard {
  readonly project = input.required<ProjectState>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly now = inject(NowService).now;
  protected readonly busy = signal<string | null>(null);
  protected readonly logsFor = signal<ServiceRow | null>(null);
  protected readonly openable = openableUrl;
  protected readonly portOf = port;
  protected readonly running = computed(() => this.project().services.filter((s) => s.status === 'running').length);
  /** The open sheet follows the service's latest row (its state changes while the logs are open). */
  protected readonly logsService = computed(() => {
    const open = this.logsFor();
    return open ? (this.project().services.find((s) => s.id === open.id) ?? open) : null;
  });
  private readonly titles = computed(() => new Map(this.project().threads.map((t) => [t.id, t.title ?? 'thread'])));
  private readonly sourceLabels = computed(() => new Map(this.project().sources.map((x) => [x.id, x.label])));

  protected stateOf(s: ServiceRow): { tone: 'run' | 'fail' | 'off'; text: string } {
    return serviceState(s, this.now());
  }

  protected origin(s: ServiceRow): string {
    return s.source_id ? (this.sourceLabels().get(s.source_id) ?? 'a removed folder') : (this.titles().get(s.agent_id) ?? 'an archived thread');
  }

  /** Opens the URL in a new tab, synchronously inside this click (DeskBridge does it before any await). */
  protected openUrl(url: string): void {
    this.bridge.call('app.openExternal', { url }).catch((err: unknown) => this.toasts.error(err));
  }

  protected async act(s: ServiceRow, what: 'start' | 'stop' | 'restart'): Promise<void> {
    this.busy.set(`${s.id}:${what}`);
    try {
      await (what === 'start' ? this.bridge.call('services.start', { id: s.id }) : what === 'stop' ? this.bridge.call('services.stop', { id: s.id }) : this.bridge.call('services.restart', { id: s.id }));
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(null);
    }
  }
}
