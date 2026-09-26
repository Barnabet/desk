import { ChangeDetectionStrategy, Component, DestroyRef, ViewEncapsulation, computed, inject, output, signal, type OnInit } from '@angular/core';
import type { ModelEndpointStatus, ModelEndpointTestResult } from '@desk/protocol';
import { DeskBridge, DeskCallError } from '../core/desk-bridge';
import { Button } from './button';
import { Field } from './field';
import { describeError } from './toast';

export type EndpointState = ModelEndpointStatus | 'unsupported' | null;

const SOURCE_LABEL: Record<NonNullable<ModelEndpointStatus['source']>, string> = {
  env: 'the DESK_OPENAI_* environment variables',
  file: '~/.config/cliproxyapi.env',
  keychain: 'your Keychain',
};

/**
 * The model endpoint: where it comes from, a connection test, and a form that writes a new base URL and key (the key
 * goes to the Keychain and is never shown). Used by onboarding and System (EndpointPanel.tsx).
 */
@Component({
  selector: 'div[deskEndpointPanel]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [Button, Field],
  host: { class: 'endpoint' },
  template: `
    @if (status() === 'unsupported') {
      <p class="status-line">This deskd manages its model endpoint itself.</p>
    }
    @if (known(); as s) {
      @if (s.configured && !editing()) {
        <p class="status-line">
          <span class="dot ok" aria-hidden="true"></span>
          <span>Using <span class="mono">{{ s.base_url }}</span> from {{ s.source ? sourceLabel[s.source] : 'the daemon' }}.</span>
        </p>
        <div class="actions">
          <button deskButton size="sm" [pending]="pending() === 'test'" (click)="test()">Test connection</button>
          <button deskButton size="sm" variant="ghost" (click)="editing.set(true)">{{ s.source === 'keychain' ? 'Change key' : 'Use a different endpoint' }}</button>
        </div>
      }
      @if (!s.configured || editing()) {
        <form class="sheet-body" novalidate (submit)="save($event)">
          @if (s.configured && s.source !== 'keychain') {
            <p class="field-hint">Saving stores this endpoint in your Keychain. Environment variables, if set, still take precedence when deskd starts.</p>
          }
          <div deskField id="endpoint-url" label="Base URL">
            <input id="endpoint-url" class="input mono" type="url" [value]="baseUrl()" (input)="baseUrl.set(val($event))" />
          </div>
          <div deskField id="endpoint-key" label="API key" hint="Stored in the Keychain; Desk never displays it.">
            <input id="endpoint-key" class="input mono" type="password" autocomplete="off" spellcheck="false" [value]="apiKey()" (input)="apiKey.set(val($event))" />
          </div>
          <div class="actions">
            <button deskButton type="submit" variant="primary" size="sm" [pending]="pending() === 'save'" [disabled]="!apiKey() || !baseUrl()">Save and test</button>
            @if (editing()) {
              <button deskButton size="sm" variant="ghost" (click)="cancelEdit()">Cancel</button>
            }
          </div>
        </form>
      }
    }
    @if (result(); as r) {
      @if (r.ok) {
        <p class="status-line"><span class="dot ok" aria-hidden="true"></span>{{ connected(r) }}</p>
      } @else {
        <p class="field-error" role="alert">Couldn’t connect: {{ r.error ?? 'unknown error' }}</p>
      }
    }
    @if (error()) {
      <p class="field-error" role="alert">{{ error() }}</p>
    }
  `,
})
export class EndpointPanel implements OnInit {
  /** React's `onStatus`: the status once loaded (or 'unsupported'), and again after a save. */
  readonly statusChanged = output<EndpointState>();

  private readonly bridge = inject(DeskBridge);
  protected readonly sourceLabel = SOURCE_LABEL;
  protected readonly status = signal<EndpointState>(null);
  /** The status, when deskd reported one. */
  protected readonly known = computed(() => {
    const s = this.status();
    return s !== null && s !== 'unsupported' ? s : null;
  });
  protected readonly editing = signal(false);
  protected readonly baseUrl = signal('http://127.0.0.1:8317/v1');
  protected readonly apiKey = signal('');
  protected readonly result = signal<ModelEndpointTestResult | null>(null);
  protected readonly pending = signal<'test' | 'save' | null>(null);
  protected readonly error = signal<string | null>(null);
  /** False once destroyed (Skip before the endpoint loaded): a late load or save then reports nothing, as React ignores it. */
  private live = true;

  constructor() {
    inject(DestroyRef).onDestroy(() => (this.live = false));
  }

  ngOnInit(): void {
    this.bridge
      .call('config.endpoint', {})
      .then((s) => {
        this.setStatus(s);
        if (s.base_url) this.baseUrl.set(s.base_url);
      })
      .catch((err: unknown) => {
        if (err instanceof DeskCallError && (err.code === 'unsupported' || err.status === 501)) this.setStatus('unsupported');
        else this.error.set(describeError(err).message);
      });
  }

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected connected(r: ModelEndpointTestResult): string {
    const n = r.models?.length ?? 0;
    return `Connected. ${n} model${n === 1 ? '' : 's'} available.`;
  }

  protected cancelEdit(): void {
    this.editing.set(false);
    this.apiKey.set('');
  }

  protected async test(): Promise<void> {
    this.pending.set('test');
    this.error.set(null);
    try {
      this.result.set(await this.bridge.call('config.testEndpoint', {}));
    } catch (err) {
      this.error.set(describeError(err).message);
    } finally {
      this.pending.set(null);
    }
  }

  protected async save(e: Event): Promise<void> {
    e.preventDefault();
    this.pending.set('save');
    this.error.set(null);
    try {
      const saved = await this.bridge.call('config.saveEndpoint', { base_url: this.baseUrl().trim(), api_key: this.apiKey() });
      this.apiKey.set('');
      this.setStatus(saved);
      this.editing.set(false);
      this.result.set(await this.bridge.call('config.testEndpoint', {}));
    } catch (err) {
      this.error.set(describeError(err).message);
    } finally {
      this.pending.set(null);
    }
  }

  private setStatus(s: EndpointState): void {
    if (!this.live) return;
    this.status.set(s);
    this.statusChanged.emit(s);
  }
}
