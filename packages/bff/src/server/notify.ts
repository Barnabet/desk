import { clip, type AttentionItem } from '@desk/protocol';

const HEADLINE: Record<AttentionItem['kind'], string> = {
  approval: 'approval needed',
  question: 'Desk has a question',
  needs_you: 'needs you',
  stalled: 'a thread stalled',
  failed: 'a thread failed',
  paused: 'agents paused',
};

/** The system notification for a new attention item (agent text is clipped, never rendered as markup). */
export function notificationFor(item: AttentionItem): { title: string; body: string } {
  return { title: clip(`${item.project_name}: ${HEADLINE[item.kind]}`, 80), body: clip(item.title, 200) };
}

export const attentionRoute = (item: AttentionItem): string => `#/attention?item=${encodeURIComponent(item.id)}`;
