import { emptyChat, emptyMessages, emptyTimeline } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import type { SessionState } from '../core/session.service';

/** A loaded project session, for specs of components that take `s` directly (the desktop's test/session.ts). */
export function sessionOf(events: StoredEvent[] = [], over: Partial<SessionState> = {}): SessionState {
  return { status: 'ready', error: null, project: null, chat: emptyChat('p'), timeline: emptyTimeline('p'), messages: emptyMessages(), events, streams: {}, ...over };
}
