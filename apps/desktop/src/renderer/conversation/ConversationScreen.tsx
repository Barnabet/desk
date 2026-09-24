import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { call } from '../bridge';
import { EmptyState } from '../components/EmptyState';
import { toastError } from '../components/Toast';
import { plural } from '../format';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { useSession } from '../state/session';
import { markSeen } from '../state/unread';
import { useWidth } from '../state/width';
import { ChatItemView, chatDomId, chatEventId } from './ChatItems';
import { Composer } from './Composer';
import { LineDiagram } from './LineDiagram';
import { lineGeometry } from './lineGeometry';
import { PlanPanel } from './PlanPanel';
import './conversation.css';

function useDraft(projectId: string): [string, (v: string | ((d: string) => string)) => void] {
  const key = `desk.draft.${projectId}`;
  const [draft, setDraftState] = useState(() => {
    try {
      return localStorage.getItem(key) ?? '';
    } catch {
      return '';
    }
  });
  useEffect(() => {
    try {
      if (draft) localStorage.setItem(key, draft);
      else localStorage.removeItem(key);
    } catch {
      // Drafts are a convenience.
    }
  }, [key, draft]);
  return [draft, setDraftState];
}

export function ConversationScreen({ projectId }: { projectId: string }) {
  const s = useSession(projectId);
  const attention = useGlobal((g) => g.attention);
  const proxy = useGlobal((g) => g.system.proxy);
  const now = useNow();
  const [draft, setDraft] = useDraft(projectId);
  const [pending, setPending] = useState<string[]>([]);
  const [answering, setAnswering] = useState<string | null>(null);
  const [planOpen, setPlanOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pinned = useRef(true);
  const width = useWidth(rootRef);

  const attentionIds = useMemo(() => new Set(attention.filter((i) => i.project_id === projectId).map((i) => i.id)), [attention, projectId]);
  const threads = s.project?.threads;
  const geometry = useMemo(() => lineGeometry({ timeline: s.timeline, threads: threads ?? [], now, width }), [s.timeline, threads, now, width]);

  useEffect(() => markSeen(projectId), [projectId, s.events.length]);
  useEffect(() => {
    if (!pending.length) return;
    const sent = new Set(s.chat.items.filter((i) => i.kind === 'user').slice(-20).map((i) => (i.kind === 'user' ? i.text : '')));
    setPending((p) => p.filter((t) => !sent.has(t)));
  }, [s.chat.items, pending.length]);
  useLayoutEffect(() => {
    const el = listRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [s.chat.items, pending]);

  if (s.status === 'loading') return <div className="page muted">Loading…</div>;
  if (s.status === 'missing')
    return (
      <div className="page">
        <EmptyState title="This project isn't here" action={<a href="#/map">Back to the map</a>}>
          It may have been archived.
        </EmptyState>
      </div>
    );
  if (s.status === 'error' || !s.project)
    return (
      <div className="page">
        <EmptyState title="Couldn't load this project">{s.error}</EmptyState>
      </div>
    );

  const project = s.project;
  const live = project.threads.filter((t) => !t.archived_at);
  const count = (st: string) => live.filter((t) => t.status === st).length;
  const answer = async (text: string) => {
    setAnswering(text);
    try {
      await call('projects.send', { id: projectId, text });
      setPending((p) => [...p, text]);
    } catch (err) {
      toastError(err);
    } finally {
      setAnswering(null);
    }
  };
  const onStation = (st: { eventId: number }) => {
    const target = s.chat.items.find((i) => chatEventId(i) >= st.eventId);
    const el = target ? document.getElementById(chatDomId(target.id)) : null;
    el?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    el?.classList.add('flash');
    setTimeout(() => el?.classList.remove('flash'), 1200);
  };

  return (
    <div className="conversation" ref={rootRef}>
      <LineDiagram g={geometry} project={project} now={now} onStation={onStation} />
      <div className={`conv-body${planOpen ? ' plan-open' : ''}`}>
        <div className="conv-intro">
          <h1>{project.project.name}</h1>
          <p className="muted">
            Desk and {plural(live.length, 'thread')}. {count('running')} running, {count('waiting')} waiting, {count('done')} done.
          </p>
          <button type="button" className="btn btn-secondary btn-sm plan-toggle" aria-expanded={planOpen} onClick={() => setPlanOpen((v) => !v)}>
            Plan
          </button>
        </div>
        <section className="conv-chat" aria-label="Conversation with Desk">
          <div
            className="chat-list"
            ref={listRef}
            onScroll={(e) => {
              const el = e.currentTarget;
              pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
            }}
          >
            {s.chat.items.length === 0 && !pending.length ? (
              <EmptyState title="Brief Desk">Say what you want done. Desk plans it, splits it into threads, and reports back.</EmptyState>
            ) : null}
            {s.chat.items.map((item) => (
              <div key={item.id} id={chatDomId(item.id)} className="chat-item">
                <ChatItemView
                  item={item}
                  projectId={projectId}
                  attentionIds={attentionIds}
                  answering={answering}
                  onAnswer={(t) => void answer(t)}
                  onOwnWords={() => textareaRef.current?.focus()}
                />
              </div>
            ))}
            {pending.map((t) => (
              <div key={t} className="chat-item">
                <div className="chat-user pending">
                  <span className="chat-meta">You · sending…</span>
                  <p>{t}</p>
                </div>
              </div>
            ))}
          </div>
          <Composer projectId={projectId} draft={draft} setDraft={setDraft} textareaRef={textareaRef} onSent={(t) => setPending((p) => [...p, t])} />
        </section>
        <PlanPanel project={project} proxyDown={proxy === 'down'} />
      </div>
    </div>
  );
}
