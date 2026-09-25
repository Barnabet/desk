import { imageLabel, type StoredEvent, type StreamServerMessage } from '@desk/protocol';

const clip = (s: string, n: number) => {
  const flat = s.replace(/\s+/g, ' ').trim();
  return flat.length > n ? `${flat.slice(0, n)}…` : flat;
};

function summarizeArgs(name: string, raw: string): string {
  let args: Record<string, unknown>;
  try {
    args = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return clip(raw, 80);
  }
  const s = (k: string) => (typeof args[k] === 'string' ? (args[k] as string) : '');
  switch (name) {
    case 'spawn_thread':
      return `"${s('title')}"`;
    case 'message_thread':
      return `${s('thread_id')} [${s('kind') || 'note'}] ${clip(s('text'), 60)}`;
    case 'bash':
    case 'bash_readonly':
    case 'bash_background':
      return clip(s('command'), 80);
    case 'read_file':
    case 'write_file':
    case 'edit_file':
    case 'library_read':
    case 'library_publish':
      return s('path');
    case 'web_fetch':
      return s('url');
    case 'web_search':
    case 'memory_search':
      return `"${s('query')}"`;
    case 'view_image':
      return Array.isArray(args.paths) ? clip(args.paths.filter((p) => typeof p === 'string').join(' '), 80) : '';
    default:
      return clip(raw === '{}' ? '' : raw, 80);
  }
}

/** `deskId` is the agent whose voice is rendered as the main speaker (`label`, default "desk"). */
export type RenderOptions = { deskId: string; label?: string; verbose?: boolean };

/** Turns stream messages into terminal lines. Stateful: tracks streamed text and thread titles. */
export function createRenderer(write: (s: string) => void, opts: RenderOptions): (m: StreamServerMessage) => void {
  const speaker = opts.label ?? 'desk';
  const titles = new Map<string, string>();
  const streamedRuns = new Set<string>();
  let streaming = false;
  const line = (s: string) => {
    if (streaming) {
      write('\n');
      streaming = false;
    }
    write(`${s}\n`);
  };
  const title = (id: string | null) => (id ? (titles.get(id) ?? id) : '?');

  const onEvent = (e: StoredEvent) => {
    const isDesk = e.agent_id === opts.deskId;
    switch (e.type) {
      case 'agent.created':
        if (e.agent_id) titles.set(e.agent_id, e.payload.title ?? e.agent_id);
        if (e.payload.role === 'thread') line(`  + thread "${e.payload.title}" (${e.agent_id}) [${e.payload.model}]`);
        return;
      case 'message.user':
        line(isDesk ? `\nyou › ${e.payload.text}` : `you → "${title(e.agent_id)}": ${e.payload.text}`);
        return;
      case 'message.agent': {
        // Threads message each other too: the sender is Desk or the thread the message came from.
        const from = e.payload.from_agent_id === opts.deskId ? 'Desk' : `"${title(e.payload.from_agent_id)}"`;
        if (isDesk) line(`  ↳ [${e.payload.from_label} — ${e.payload.kind}] ${clip(e.payload.text, 300)}`);
        else if (opts.verbose) line(`  ↦ ${from} → "${title(e.agent_id)}" [${e.payload.kind}] ${clip(e.payload.text, 200)}`);
        return;
      }
      case 'assistant.message':
        if (!isDesk && !opts.verbose) return;
        if (e.payload.content && !streamedRuns.has(e.payload.run_id)) line(`${isDesk ? speaker : `"${title(e.agent_id)}"`} › ${e.payload.content}`);
        else if (streaming) line('');
        for (const tc of e.payload.tool_calls) line(`  ⚙ ${tc.name} ${summarizeArgs(tc.name, tc.arguments)}`.trimEnd());
        return;
      case 'tool.result':
        // Tool calls print with the assistant message; a result adds only the images the agent looked at.
        if (!isDesk && !opts.verbose) return;
        for (const image of e.payload.images ?? []) line(`    [image: ${imageLabel(image)}]`);
        return;
      case 'images.withheld':
        if (!isDesk && !opts.verbose) return;
        line(`  ⚠ no longer sent to the model: ${e.payload.images.map((i) => i.name).join(', ')} (${clip(e.payload.reason, 120)})`);
        return;
      case 'approval.requested': {
        let detail = e.payload.arguments;
        try {
          detail = summarizeArgs(e.payload.tool, e.payload.arguments);
        } catch {}
        line(`  ! approval ${e.payload.approval_id}: ${e.payload.tool} ${detail} — ${e.payload.reason}`);
        return;
      }
      case 'approval.resolved':
        line(`  ✓ approval ${e.payload.approval_id} ${e.payload.decision} by ${e.payload.resolved_by}${e.payload.note ? `: ${e.payload.note}` : ''}`);
        return;
      case 'agent.status_changed':
        if (!isDesk && ['done', 'failed', 'cancelled', 'waiting'].includes(e.payload.status)) {
          line(`  • "${title(e.agent_id)}" → ${e.payload.status}${e.payload.reason && e.payload.status !== 'done' ? ` (${clip(e.payload.reason, 120)})` : ''}`);
        }
        return;
      case 'run.finished':
        if (isDesk && e.payload.reason === 'error') line(`  ✗ Desk run failed: ${e.payload.detail ?? ''}`);
        return;
      case 'report':
        line(
          [
            `\n┌ ${e.payload.headline}`,
            ...(e.payload.progress ? [`│ ${e.payload.progress}`] : []),
            ...(e.payload.needs_you.length ? [`│ needs you: ${e.payload.needs_you.join('; ')}`] : []),
            ...e.payload.results.map((r) => `│ • ${r}`),
            '└',
          ].join('\n'),
        );
        return;
      case 'question.asked':
        line(`  ? ${e.payload.question}${e.payload.options?.length ? ` [${e.payload.options.join(' / ')}]` : ''}`);
        return;
      case 'artifact.published':
        line(`  ▣ library: ${e.payload.path} — ${e.payload.title}`);
        return;
      default:
        return;
    }
  };

  return (m) => {
    if (m.kind === 'event') onEvent(m.event);
    else if (m.kind === 'ephemeral' && m.event.agent_id === opts.deskId) {
      if (!streaming) {
        write(`${speaker} › `);
        streaming = true;
      }
      streamedRuns.add(m.event.payload.run_id);
      write(m.event.payload.text);
    } else if (m.kind === 'error') line(`  ✗ stream error: ${m.message}`);
  };
}
