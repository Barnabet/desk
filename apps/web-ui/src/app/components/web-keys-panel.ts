import { ChangeDetectionStrategy, Component, inject, input, output, signal, ViewEncapsulation, type OnInit } from '@angular/core';
import type { WebKeysStatus, WebKeyTestResult, WebProvider } from '@desk/protocol';
import { WEB_KEY_ENV, webKeyRows, type WebKeyRow } from '@desk/ui-core';
import { DeskBridge, DeskCallError } from '../core/desk-bridge';
import { Button } from './button';
import { ConfirmDialog } from './confirm-dialog';
import { ExternalLink } from './external-link';
import { Field } from './field';
import { describeError } from './toast';

type KeyStatus = WebKeysStatus[WebProvider];

/** One provider: its status, a key field (unless the key comes from deskd's environment), Test, Remove, and where to get a key (KeyRow in WebKeysPanel.tsx). */
@Component({
  selector: 'div[deskWebKeyRow]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [Button, ConfirmDialog, ExternalLink, Field],
  host: { class: 'web-key', role: 'group', '[attr.aria-labelledby]': "'web-key-' + row().provider" },
  template: `
    @let r = row();
    @let id = 'web-key-' + r.provider;
    @let set = r.statusLabel !== 'Not set';
    <p class="status-line">
      <span class="dot" [class.ok]="set" aria-hidden="true"></span>
      <strong [id]="id">{{ r.name }}</strong>
      <span class="muted">{{ r.powers }}</span>
      <span>{{ r.statusLabel }}</span>
    </p>
    @if (r.editable) {
      <form class="sheet-body" novalidate (submit)="save($event)">
        <div deskField [id]="id + '-input'" [label]="r.name + ' API key'" hint="Stored in the Keychain; Desk never displays it.">
          <input [id]="id + '-input'" class="input mono" type="password" autocomplete="off" spellcheck="false" [value]="key()" (input)="key.set(val($event))" />
        </div>
        <div class="actions">
          <button deskButton type="submit" variant="primary" size="sm" [pending]="pending() === 'save'" [disabled]="!key().trim()">Save</button>
          @if (set) {
            <button deskButton size="sm" [pending]="pending() === 'test'" (click)="test()">Test</button>
          }
          @if (r.removable) {
            <button deskButton size="sm" variant="ghost" [pending]="pending() === 'remove'" (click)="confirming.set(true)">Remove</button>
          }
          <span deskExternalLink [href]="r.dashboardUrl">Get a key</span>
        </div>
      </form>
    } @else {
      <p class="field-hint">Set by <span class="mono">{{ envName[r.provider] }}</span> in deskd’s environment.</p>
      <div class="actions">
        <button deskButton size="sm" [pending]="pending() === 'test'" (click)="test()">Test</button>
      </div>
    }
    @if (result(); as res) {
      @if (res.ok) {
        <p class="status-line"><span class="dot ok" aria-hidden="true"></span>Key works.</p>
      } @else {
        <p class="field-error" role="alert">Key test failed: {{ res.error ?? 'unknown error' }}</p>
      }
    }
    @if (error()) {
      <p class="field-error" role="alert">{{ error() }}</p>
    }
    @if (confirming()) {
      <div deskConfirmDialog [title]="'Remove the ' + r.name + ' key?'" confirmLabel="Remove" danger (cancel)="confirming.set(false)" (confirm)="remove()">
        <p class="subtitle">Agents fall back to the keyless engines for what {{ r.name }} did until you add a key again.</p>
      </div>
    }
  `,
})
export class WebKeyRowComponent {
  readonly row = input.required<WebKeyRow>();
  /** React's `onStatus`: the provider's new status after a save or removal. */
  readonly statusChanged = output<{ provider: WebProvider; status: KeyStatus }>();

  private readonly bridge = inject(DeskBridge);
  protected readonly envName = WEB_KEY_ENV;
  protected readonly key = signal('');
  protected readonly pending = signal<'save' | 'test' | 'remove' | null>(null);
  protected readonly result = signal<WebKeyTestResult | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly confirming = signal(false);

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  private async run(what: 'save' | 'test' | 'remove', fn: () => Promise<void>): Promise<void> {
    this.pending.set(what);
    this.error.set(null);
    try {
      await fn();
    } catch (err) {
      this.error.set(describeError(err).message);
    } finally {
      this.pending.set(null);
    }
  }

  protected save(e: Event): void {
    e.preventDefault();
    const provider = this.row().provider;
    void this.run('save', async () => {
      const status = await this.bridge.call('config.saveWebKey', { provider, key: this.key() });
      this.key.set('');
      this.result.set(null);
      this.statusChanged.emit({ provider, status });
    });
  }

  protected test(): void {
    void this.run('test', async () => this.result.set(await this.bridge.call('config.testWebKey', { provider: this.row().provider })));
  }

  protected remove(): void {
    this.confirming.set(false);
    const provider = this.row().provider;
    void this.run('remove', async () => {
      this.statusChanged.emit({ provider, status: await this.bridge.call('config.removeWebKey', { provider }) });
      this.result.set(null);
    });
  }
}

/** System → Web search and fetch: the Serper and Firecrawl keys, stored by deskd in the Keychain and never shown (WebKeysPanel.tsx). */
@Component({
  selector: 'div[deskWebKeysPanel]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [WebKeyRowComponent],
  host: { class: 'web-keys' },
  template: `
    <p class="field-hint">Agents search with Serper and read pages with Firecrawl. Without a key, they fall back to keyless search engines and plain fetches.</p>
    @if (status() === 'unsupported') {
      <p class="status-line">This deskd takes its web keys from its environment.</p>
    }
    @for (row of rows(); track row.provider) {
      <div deskWebKeyRow [row]="row" (statusChanged)="update($event.provider, $event.status)"></div>
    }
    @if (error()) {
      <p class="field-error" role="alert">{{ error() }}</p>
    }
  `,
})
export class WebKeysPanel implements OnInit {
  private readonly bridge = inject(DeskBridge);
  protected readonly status = signal<WebKeysStatus | 'unsupported' | null>(null);
  protected readonly error = signal<string | null>(null);

  protected rows(): WebKeyRow[] {
    const s = this.status();
    return s && s !== 'unsupported' ? webKeyRows(s) : [];
  }

  ngOnInit(): void {
    this.bridge
      .call('config.webKeys', {})
      .then((s) => this.status.set(s))
      .catch((err: unknown) => {
        if (err instanceof DeskCallError && (err.code === 'unsupported' || err.status === 501)) this.status.set('unsupported');
        else this.error.set(describeError(err).message);
      });
  }

  protected update(provider: WebProvider, status: KeyStatus): void {
    const prev = this.status();
    if (prev && prev !== 'unsupported') this.status.set({ ...prev, [provider]: status });
  }
}
