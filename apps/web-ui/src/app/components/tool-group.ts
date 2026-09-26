import { ChangeDetectionStrategy, Component, ViewEncapsulation, booleanAttribute, computed, input } from '@angular/core';
import type { ToolCallView } from '@desk/client';
import { summarizeToolArgs } from '@desk/protocol';
import { plural, toolNames } from '@desk/ui-core';
import { ImageThumbs } from './image-thumbs';

const LABEL: Record<ToolCallView['status'], string> = { running: 'running', ok: 'ok', error: 'error', denied: 'denied', interrupted: 'interrupted' };

/** A tool call's status: a live dot while it runs. */
@Component({
  selector: 'span[deskToolStatus]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'tool-status', '[class]': "'tool-status-' + status()" },
  // One line: the label is compared exactly, and a line break around it would become a space.
  template: `@if (status() === 'running') {<span class="live-dot" aria-hidden="true"></span>}{{ label() }}`,
})
export class ToolStatus {
  readonly status = input.required<ToolCallView['status']>();
  protected readonly label = computed(() => LABEL[this.status()]);
}

/** A call's arguments on one line. A `thread_id` that `titleOf` knows reads as that thread's title (design spec §8 item 12). */
function argsLine(c: ToolCallView, titleOf: ((id: string) => string | undefined) | undefined): string {
  if (titleOf) {
    try {
      const id: unknown = (JSON.parse(c.arguments) as { thread_id?: unknown } | null)?.thread_id;
      const title = typeof id === 'string' ? titleOf(id) : undefined;
      if (title) return title;
    } catch {
      // Not JSON: summarised as it is, below.
    }
  }
  return summarizeToolArgs(c.arguments, 70);
}

/**
 * A collapsible group of tool calls (the Narrative depth), with thumbnails of any images the tools showed, visible
 * closed. `titleOf` names the threads that calls point at by id. The host adds no box: its children are the React fragment's.
 */
@Component({
  selector: 'div[deskToolGroup]',
  imports: [ToolStatus, ImageThumbs],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents', '[attr.title]': 'null' },
  template: `
    <details class="toolgroup" [attr.open]="defaultOpen() ? '' : null">
      <summary><span class="toolgroup-title">{{ heading() }}</span><span class="toolgroup-names">{{ names() }}</span></summary>
      <ul>
        @for (c of calls(); track c.id) {
          <li><span class="mono grow">{{ c.name }} <span class="muted">{{ args(c) }}</span></span><span deskToolStatus [status]="c.status"></span></li>
        }
      </ul>
    </details>
    @if (images().length) {
      <div deskImageThumbs [images]="images()"></div>
    }
  `,
})
export class ToolGroup {
  readonly calls = input.required<ToolCallView[]>();
  readonly title = input<string>();
  readonly defaultOpen = input(false, { transform: booleanAttribute });
  readonly titleOf = input<(id: string) => string | undefined>();
  protected readonly heading = computed(() => this.title() ?? `${this.calls().some((c) => c.status === 'running') ? 'Using' : 'Used'} ${plural(this.calls().length, 'tool')}`);
  protected readonly names = computed(() => toolNames(this.calls()));
  protected readonly images = computed(() => this.calls().flatMap((c) => c.images ?? []));

  protected args(c: ToolCallView): string {
    return argsLine(c, this.titleOf());
  }
}
