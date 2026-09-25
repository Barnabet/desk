import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, input, signal, ViewEncapsulation } from '@angular/core';
import { DeskBridge } from '../core/desk-bridge';
import { ConfirmDialog } from './confirm-dialog';
import { ToastService } from './toast';

const ALLOWED = new Set(['http:', 'https:', 'mailto:']);

function linkTarget(href: string): string | null {
  try {
    const url = new URL(href);
    return ALLOWED.has(url.protocol) ? url.toString() : null;
  } catch {
    return null;
  }
}

/**
 * A link from agent content: web and mail links open in a new browser tab after a confirmation; others are inert text.
 * When the link is allowed this host is `display: contents` around its button; otherwise it is the `md-link-disabled` span.
 */
@Component({
  selector: 'span[deskExternalLink]',
  imports: [NgTemplateOutlet, ConfirmDialog],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    '[class.md-link-disabled]': 'target() === null',
    '[style.display]': "target() === null ? null : 'contents'",
  },
  template: `
    <ng-template #label><ng-content /></ng-template>
    @if (target(); as url) {
      <button type="button" class="link" [title]="url" (click)="asking.set(true)"><ng-container [ngTemplateOutlet]="label" /></button>
      @if (asking()) {
        <div deskConfirmDialog title="Open this link?" confirmLabel="Open in browser" (cancel)="asking.set(false)" (confirm)="open(url)">
          <p class="subtitle">Links in agent messages can point anywhere. Check the address first.</p>
          <p class="mono" style="margin: 0; overflow-wrap: anywhere">{{ url }}</p>
        </div>
      }
    } @else {
      <ng-container [ngTemplateOutlet]="label" />
    }
  `,
})
export class ExternalLink {
  readonly href = input.required<string>();
  protected readonly target = computed(() => linkTarget(this.href()));
  protected readonly asking = signal(false);
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);

  protected open(url: string): void {
    this.asking.set(false);
    // DeskBridge opens the tab synchronously, inside this click.
    this.bridge.call('app.openExternal', { url }).catch((err: unknown) => this.toasts.error(err));
  }
}

/** The name spec §4.10 uses. */
export { ExternalLink as ExternalLinkComponent };
