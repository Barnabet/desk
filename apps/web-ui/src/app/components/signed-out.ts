import { ChangeDetectionStrategy, Component, inject, signal, ViewEncapsulation } from '@angular/core';
import { DeskBridge } from '../core/desk-bridge';
import { EmptyState } from './empty-state';

/**
 * The page without a session secret (spec §2): nothing of Desk, only how to open it. At the phones' address it also takes
 * the short code `desk web pair` prints, which pairs a Home Screen app (its storage is not Safari's, where the QR link opens).
 */
@Component({
  selector: 'div[deskSignedOut]',
  imports: [EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  template: `
    @if (bridge.remote) {
      <div deskEmptyState title="Pair this phone" body="On your Mac, run desk web pair, then type the code it prints here. It works once, for 10 minutes.">
        <form class="pair-form" (submit)="submit($event)">
          <div class="field">
            <label for="pair-code">Pairing code</label>
            <input
              id="pair-code"
              class="input mono"
              autocomplete="one-time-code"
              autocapitalize="characters"
              spellcheck="false"
              placeholder="ABCD-EFGH"
              maxlength="12"
              [value]="code()"
              (input)="code.set($any($event.target).value)"
            />
          </div>
          <button type="submit" class="btn btn-primary" [disabled]="busy() || !code().trim()" [attr.aria-busy]="busy() ? 'true' : null">Pair this phone</button>
          @if (error()) {
            <p class="field-hint" role="alert">{{ error() }}</p>
          }
        </form>
      </div>
    } @else {
      <div
        deskEmptyState
        title="Open Desk from your terminal"
        body="Run desk web on this computer and open the link it prints. A link signs this browser in once and expires after two minutes; if desk web is already running, press Enter in its terminal for a new one, or run desk web login when it runs as a login item."
      ></div>
    }
  `,
})
export class SignedOut {
  protected readonly bridge = inject(DeskBridge);
  protected readonly code = signal('');
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected async submit(event: Event): Promise<void> {
    event.preventDefault();
    if (this.busy() || !this.code().trim()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.bridge.pair(this.code().trim());
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.busy.set(false);
    }
  }
}
