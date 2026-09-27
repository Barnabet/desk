import { emptyChat, emptyMessages, emptyTimeline } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import type { SessionState } from '../state/session';

/** A loaded project session for component tests that take `s` directly. */
export function sessionOf(events: StoredEvent[] = [], over: Partial<SessionState> = {}): SessionState {
  return { status: 'ready', error: null, project: null, chat: emptyChat('p'), timeline: emptyTimeline('p'), messages: emptyMessages(), events, streams: {}, ...over };
}
