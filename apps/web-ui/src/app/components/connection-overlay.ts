import { ChangeDetectionStrategy, Component, computed, inject, signal, ViewEncapsulation } from '@angular/core';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { Button } from './button';
import { describeError } from './toast';

type Mode = 'none' | 'web' | 'reconnecting' | 'offline' | 'mismatch';

const BANNER: Partial<Record<Mode, string>> = {
  web: 'Reconnecting to desk web… If you stopped it, run desk web again.',
  reconnecting: 'Reconnecting to deskd… Your threads keep running.',
};

/** deskd lost: an overlay with Start. Reconnecting: a calm banner. Protocol mismatch: blocks the app. Lost desk web: a banner. */
@Component({
  selector: 'div[deskConnectionOverlay]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    '[class.banner]': 'banner() !== null',
    '[class.overlay]': 'blocking()',
    '[attr.role]': "banner() !== null ? 'status' : blocking() ? 'alertdialog' : null",
    '[attr.aria-modal]': "blocking() ? 'true' : null",
    '[attr.aria-labelledby]': "blocking() ? 'overlay-title' : null",
    '[style.display]': "mode() === 'none' ? 'none' : null",
  },
  template: `
    @if (banner(); as text) {
      <span class="dot warn" aria-hidden="true"></span><ng-container>{{ text }}</ng-container>
    }
    @if (blocking()) {
      <div class="card">
        <h2 id="overlay-title" class="sheet-title">{{ mismatch() ? 'Desk and deskd are out of step' : 'Desk isn’t running' }}</h2>
        <p class="subtitle">{{ subtitle() }}</p>
        @if (error(); as message) {
          <p class="field-error" role="alert">{{ message }}</p>
        }
        <div class="actions">
          <button deskButton variant="primary" [pending]="pending() !== null" (click)="run(mismatch() ? 'restart' : 'start')">{{ mismatch() ? 'Restart deskd' : 'Start Desk' }}</button>
          <button deskButton variant="ghost" (click)="revealLogs()">Reveal logs</button>
        </div>
      </div>
    }
  `,
})
export class ConnectionOverlay {
  private readonly bridge = inject(DeskBridge);
  private readonly global = inject(GlobalStore).state;
  protected readonly mode = computed<Mode>(() => {
    if (this.bridge.pushStatus() === 'reconnecting') return 'web';
    const status = this.global().connection.status;
    return status === 'reconnecting' || status === 'offline' || status === 'mismatch' ? status : 'none';
  });
  protected readonly banner = computed(() => BANNER[this.mode()] ?? null);
  protected readonly blocking = computed(() => this.mode() === 'offline' || this.mode() === 'mismatch');
  protected readonly mismatch = computed(() => this.mode() === 'mismatch');
  protected readonly subtitle = computed(() =>
    this.mismatch()
      ? `${this.global().connection.detail ?? 'The daemon speaks a different protocol.'} Restart the daemon from this install, or update Desk.`
      : 'Your projects and threads are safe. Start the daemon to pick up where they left off.',
  );
  protected readonly pending = signal<'start' | 'restart' | null>(null);
  protected readonly error = signal<string | null>(null);

  protected run(op: 'start' | 'restart'): void {
    this.pending.set(op);
    this.error.set(null);
    this.bridge
      .call(op === 'start' ? 'daemon.start' : 'daemon.restart', {})
      .catch((err: unknown) => this.error.set(describeError(err).message))
      .finally(() => this.pending.set(null));
  }

  protected revealLogs(): void {
    this.bridge.call('app.revealLogs', {}).catch(() => {});
  }
}
