import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { PairSheet } from '../components/PairSheet';
import { navigate, replaceRoute } from '../router';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { useSession } from '../state/session';
import { useWidth } from '../state/width';
import { LineDiagram } from './LineDiagram';
import { lineGeometry } from './lineGeometry';
import './conversation.css';

/**
 * The Conversation and Threads tabs' shared frame: the timeline on top and the tab below. The conversation shows Desk's
 * line alone, so the chat gets the height; Threads shows every lane. The frame stays mounted across the two tabs, so
 * switching folds the lanes into Desk's line or unfolds them out of it.
 */
export function ProjectFrame(o: {
  projectId: string;
  /** `desk`: Desk's line and its stops; `full`: every lane, message link and mark. */
  mode: 'desk' | 'full';
  /** The thread open on the Threads tab: its lane stays lit, the others dim. */
  focus?: string;
  children: ReactNode;
}) {
  const { projectId, mode } = o;
  const s = useSession(projectId);
  const attention = useGlobal((g) => g.attention);
  const now = useNow();
  const rootRef = useRef<HTMLDivElement>(null);
  const width = useWidth(rootRef);
  /** The pair sheet's two agents (design spec §8 item 8): local state, no route. */
  const [pairOf, setPairOf] = useState<readonly [string, string] | null>(null);
  const onPair = useCallback((a: string, b: string) => setPairOf([a, b]), []);
  const projectAttention = useMemo(() => attention.filter((i) => i.project_id === projectId), [attention, projectId]);
  const threads = s.project?.threads;
  // A finished lane that is answering gets a stub (design spec §8 item 10); the set changes only when an answer run starts or ends.
  const answeringIds = useMemo(() => new Set(Object.keys(s.messages.answering)), [s.messages.answering]);
  const geometry = useMemo(
    () =>
      lineGeometry({
        timeline: s.timeline,
        threads: threads ?? [],
        now,
        width,
        answering: answeringIds,
        messages: s.messages,
      }),
    [s.timeline, threads, now, width, answeringIds, s.messages],
  );
  // A Desk stop opens the chat at that point: in place on the conversation, as a new page from Threads (Back returns).
  const onStation = useCallback(
    (st: { eventId: number }) =>
      (mode === 'desk' ? replaceRoute : navigate)({
        name: 'project',
        id: projectId,
        tab: 'conversation',
        at: st.eventId,
      }),
    [mode, projectId],
  );

  return (
    <div className="project-frame" ref={rootRef}>
      {s.status === 'ready' && s.project ? (
        <ErrorBoundary>
          <LineDiagram
            g={geometry}
            project={s.project}
            messages={s.messages}
            attention={projectAttention}
            now={now}
            mode={mode}
            focus={o.focus ?? null}
            onStation={onStation}
            onPair={onPair}
          />
        </ErrorBoundary>
      ) : null}
      <div className="project-frame-body">{o.children}</div>
      {pairOf ? <PairSheet projectId={projectId} messages={s.messages} a={pairOf[0]} b={pairOf[1]} onClose={() => setPairOf(null)} /> : null}
    </div>
  );
}
