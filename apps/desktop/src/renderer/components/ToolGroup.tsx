import type { ToolCallView } from '@desk/client';
import { summarizeToolArgs } from '@desk/protocol';
import { plural, toolNames } from '@desk/ui-core';
import { ImageThumbs } from './ImageThumbs';

const LABEL: Record<ToolCallView['status'], string> = { running: 'running', ok: 'ok', error: 'error', denied: 'denied', interrupted: 'interrupted' };

export function ToolStatus({ status }: { status: ToolCallView['status'] }) {
  return (
    <span className={`tool-status tool-status-${status}`}>
      {status === 'running' ? <span className="live-dot" aria-hidden="true" /> : null}
      {LABEL[status]}
    </span>
  );
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
 * closed. `titleOf` names the threads that calls point at by id.
 */
export function ToolGroup({
  calls,
  title,
  defaultOpen = false,
  titleOf,
}: {
  calls: ToolCallView[];
  title?: string;
  defaultOpen?: boolean;
  titleOf?: (id: string) => string | undefined;
}) {
  const live = calls.some((c) => c.status === 'running');
  const images = calls.flatMap((c) => c.images ?? []);
  return (
    <>
      <details className="toolgroup" open={defaultOpen || undefined}>
        <summary>
          <span className="toolgroup-title">{title ?? `${live ? 'Using' : 'Used'} ${plural(calls.length, 'tool')}`}</span>
          <span className="toolgroup-names">{toolNames(calls)}</span>
        </summary>
        <ul>
          {calls.map((c) => (
            <li key={c.id}>
              <span className="mono grow">
                {c.name} <span className="muted">{argsLine(c, titleOf)}</span>
              </span>
              <ToolStatus status={c.status} />
            </li>
          ))}
        </ul>
      </details>
      {images.length ? <ImageThumbs images={images} /> : null}
    </>
  );
}
