import { ChangeDetectionStrategy, Component, DestroyRef, inject, input, signal, ViewEncapsulation } from '@angular/core';

/** Fenced code from agent text or tool calls, with its language and a Copy button. */
@Component({
  selector: 'div[deskCodeBlock]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'codeblock' },
  template: `
    <div class="codeblock-bar">
      <span class="codeblock-lang">{{ language() ?? '' }}</span>
      <button type="button" class="codeblock-copy" (click)="copy()">{{ copied() ? 'Copied' : 'Copy' }}</button>
    </div>
    <pre><code>{{ code() }}</code></pre>
  `,
})
export class CodeBlock {
  readonly code = input.required<string>();
  readonly language = input<string>();
  protected readonly copied = signal(false);
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.timer));
  }

  protected copy(): void {
    void navigator.clipboard
      ?.writeText(this.code())
      .then(() => {
        this.copied.set(true);
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.copied.set(false), 1500);
      })
      .catch(() => {});
  }
}

/** The name spec §4.10 uses. */
export { CodeBlock as CodeBlockComponent };
