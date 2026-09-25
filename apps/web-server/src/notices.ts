import type { AttentionItem } from '@desk/protocol';
import { attentionRoute, notificationFor } from '@desk/bff/server';
import type { WebNotice } from './frames';

/** Browser notifications for new attention items: the desktop wording, at most three, tagged by item so tabs show one. */
export function webNotices(items: AttentionItem[]): WebNotice[] {
  return items.slice(0, 3).map((item) => ({ tag: item.id, ...notificationFor(item), route: attentionRoute(item) }));
}
