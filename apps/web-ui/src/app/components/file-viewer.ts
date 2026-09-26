import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, effect, inject, input, linkedSignal, signal } from '@angular/core';
import { asText, bytes, extOf, isMarkdown } from '@desk/ui-core';
import { DeskBridge } from '../core/desk-bridge';
import { Button } from './button';
import { CodeBlock } from './code-block';
import { SafeMarkdown } from './safe-markdown';
import { ToastService } from './toast';

/**
 * The image types shown as images (spec §4.11). A browser lets the user open an image in a tab of its own, where a
 * `blob:` document runs with desk web's origin, so SVG (a document that can run script) is shown as its source instead.
 * A Map, not an object literal: a file named `x.constructor` or `x.__proto__` must not find Object.prototype's members.
 */
const RASTER = new Map([
  ['png', 'image/png'],
  ['jpg', 'image/jpeg'],
  ['jpeg', 'image/jpeg'],
  ['gif', 'image/gif'],
  ['webp', 'image/webp'],
]);

/** The raster image type of `path`, by its extension; null for anything else, SVG included. */
export const rasterMime = (path: string): string | null => RASTER.get(extOf(path)) ?? null;

/**
 * Shows one file's bytes: raster images through a blob URL of their own type (never a remote one), Markdown rendered
 * safely (with a Raw toggle), other text (SVG included) as code, and anything else as a download. What the host projects
 * sits in the bar before "Save a copy…" (the React `actions`).
 */
@Component({
  selector: 'div[deskFileViewer]',
  imports: [Button, CodeBlock, SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'file-viewer' },
  template: `
    <div class="file-viewer-bar">
      <span class="mono grow">{{ path() }}</span>
      <span class="muted small">{{ size() }}</span>
      @if (md()) {
        <button deskButton size="sm" variant="ghost" (click)="raw.set(!raw())">{{ raw() ? 'Rendered' : 'Raw' }}</button>
      }
      <ng-content />
      <button deskButton size="sm" (click)="save()">Save a copy…</button>
    </div>
    @if (mime()) {
      @if (url(); as u) {
        <img class="file-image" [src]="u" [alt]="name()" />
      }
    } @else if (text() === null) {
      <p class="muted">This file isn't text, so it can't be shown here. Save a copy to open it.</p>
    } @else if (md() && !raw()) {
      <div deskSafeMarkdown [text]="text() ?? ''"></div>
    } @else {
      <div deskCodeBlock [code]="text() ?? ''" [language]="ext()"></div>
    }
  `,
})
export class FileViewer {
  readonly path = input.required<string>();
  readonly data = input.required<Uint8Array>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly text = computed(() => asText(this.data()));
  protected readonly mime = computed(() => rasterMime(this.path()));
  protected readonly md = computed(() => this.text() !== null && isMarkdown(this.path()));
  protected readonly ext = computed(() => extOf(this.path()));
  protected readonly name = computed(() => this.path().split('/').pop() ?? '');
  /** The download's name: 'file' for a path that ends in '/'. */
  private readonly saveName = computed(() => this.name() || 'file');
  protected readonly size = computed(() => bytes(this.data().length));
  /** The Markdown source instead of its rendering; each new file starts rendered. */
  protected readonly raw = linkedSignal({ source: this.path, computation: () => false });
  protected readonly url = signal<string | null>(null);

  constructor() {
    // Only raster bytes become an image, typed as what they claim to be; the URL is revoked when the file changes.
    effect((onCleanup) => {
      const mime = this.mime();
      const data = this.data();
      if (!mime) {
        this.url.set(null);
        return;
      }
      const u = URL.createObjectURL(new Blob([new Uint8Array(data)], { type: mime }));
      this.url.set(u);
      onCleanup(() => URL.revokeObjectURL(u));
    });
  }

  /** DeskBridge downloads it as application/octet-stream and revokes the URL once the click's task is over (spec §4.11). */
  protected async save(): Promise<void> {
    try {
      await this.bridge.call('app.saveFile', { name: this.saveName(), data: new Uint8Array(this.data()) });
    } catch (err) {
      this.toasts.error(err);
    }
  }
}
