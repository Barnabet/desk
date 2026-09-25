import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { call } from '../bridge';
import { EmptyState } from '../components/EmptyState';
import { PairSheet } from '../components/PairSheet';
import { toastError } from '../components/Toast';
import { plural } from '../format';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { useSession } from '../state/session';
import { markSeen } from '../state/unread';
import { replaceRoute } from '../router';
import { ChatItemView, chatDomId, chatEventId } from './ChatItems';
import { keepStable, rowViews, ticks, type RowView } from './rowViews';
import { Composer, useAttachments, useFileDrop } from './Composer';
import { PlanPanel } from './PlanPanel';
import { ServicesCard } from './ServicesCard';
import { WhatsUp } from './WhatsUp';
import { useMediaQuery } from '../state/media';
import './conversation.css';

/** Chat items rendered at first; scrolling up renders this many more (long conversations stay fast). */
export const CHAT_PAGE = 60;

/** Unchanged chat items keep their identity across events, so only new or updated ones re-render. */
const ChatRow = memo(ChatItemView);

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

/** The chat with Desk, its plan and services. `at`: a Desk stop's event id to scroll the chat to (from the timeline). */
export function ConversationScreen({ projectId, at }: { projectId: string; at?: number }) {
  const s = useSession(projectId);
  const attention = useGlobal((g) => g.attention);
  const proxy = useGlobal((g) => g.system.proxy);
  const now = useNow();
  const [draft, setDraft] = useDraft(projectId);
  const [pending, setPending] = useState<string[]>([]);
  const [answering, setAnswering] = useState<string | null>(null);
  // Services sit at the bottom of the left column; on narrow windows that column collapses, so they join the plan overlay.
  const narrow = useMediaQuery('(max-width: 1279px)');
  const [planOpen, setPlanOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const attachments = useAttachments(projectId, setDraft);
  const drop = useFileDrop((files) => void attachments.attach(files));
  const pinned = useRef(true);
  const [shown, setShown] = useState(CHAT_PAGE);
  /** Scroll position to keep while older items are rendered above it. */
  const anchor = useRef<{ height: number; top: number } | null>(null);
  const [jumpTo, setJumpTo] = useState<string | null>(null);
  const answerRef = useRef<(text: string) => Promise<void>>(async () => {});
  const onAnswer = useCallback((t: string) => void answerRef.current(t), []);
  const onOwnWords = useCallback(() => textareaRef.current?.focus(), []);
  const jumpRef = useRef<(itemId: string) => void>(() => {});
  const atRef = useRef<() => void>(() => {});
  const onJump = useCallback((itemId: string) => jumpRef.current(itemId), []);
  /** The pair sheet's two agents (design spec §8 item 8): local state, no route. */
  const [pairOf, setPairOf] = useState<readonly [string, string] | null>(null);
  const onPair = useCallback((a: string, b: string) => setPairOf([a, b]), []);
  /** Last render's row views: a row whose view did not change keeps its object, so ChatRow's memo skips it (design spec §7). */
  const viewsRef = useRef<Map<string, RowView>>(new Map());
  const views = useMemo(() => (viewsRef.current = keepStable(viewsRef.current, rowViews(s.chat.items, s.messages))), [s.chat.items, s.messages]);

  const projectAttention = useMemo(() => attention.filter((i) => i.project_id === projectId), [attention, projectId]);
  const attentionIds = useMemo(() => new Set(projectAttention.map((i) => i.id)), [projectAttention]);

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
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el || !anchor.current) return;
    el.scrollTop = el.scrollHeight - anchor.current.height + anchor.current.top;
    anchor.current = null;
  }, [shown]);
  useEffect(() => {
    if (at !== undefined && s.status === 'ready') atRef.current();
  }, [at, s.status]);
  useEffect(() => {
    if (!jumpTo) return;
    const el = document.getElementById(chatDomId(jumpTo));
    setJumpTo(null);
    el?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    el?.classList.add('flash');
    setTimeout(() => el?.classList.remove('flash'), 1200);
  }, [jumpTo]);

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
  answerRef.current = answer;
  const items = s.chat.items;
  const start = Math.max(0, items.length - shown);
  const showEarlier = () => {
    const el = listRef.current;
    if (el) anchor.current = { height: el.scrollHeight, top: el.scrollTop };
    setShown((n) => n + CHAT_PAGE);
  };
  /** Scrolls to the item at `index` (rendering older items first when it is not shown yet) and flashes it. */
  const jumpToIndex = (index: number) => {
    if (index < 0) return;
    pinned.current = false;
    if (index < start) setShown(items.length - index + 5);
    setJumpTo(items[index]!.id);
  };
  // A Desk stop clicked on the timeline arrives as `at`: jump there once, then drop it so the same stop can jump again.
  atRef.current = () => {
    if (at === undefined) return;
    jumpToIndex(items.findIndex((i) => chatEventId(i) >= at));
    replaceRoute({ name: 'project', id: projectId, tab: 'conversation' });
  };
  jumpRef.current = (itemId) => jumpToIndex(items.findIndex((i) => i.id === itemId));

  return (
    <div className="conversation">
      <div className={`conv-body${planOpen ? ' plan-open' : ''}`}>
        <div className="conv-intro">
          <WhatsUp project={project} now={now} />
          <button type="button" className="btn btn-secondary btn-sm plan-toggle" aria-expanded={planOpen} onClick={() => setPlanOpen((v) => !v)}>
            Plan
            {project.services.some((x) => x.status === 'running') ? ` · ${plural(project.services.filter((x) => x.status === 'running').length, 'service')}` : ''}
          </button>
          {narrow ? null : <ServicesCard project={project} />}
        </div>
        <section className={`conv-chat${drop.dropping ? ' dropping' : ''}`} aria-label="Conversation with Desk" {...drop.handlers}>
          {drop.dropping ? (
            <div className="chat-drop" aria-hidden="true">
              Drop to attach to the Library
            </div>
          ) : null}
          <div
            className="chat-list"
            ref={listRef}
            onScroll={(e) => {
              const el = e.currentTarget;
              pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
              if (el.scrollTop < 400 && start > 0 && !anchor.current) showEarlier();
            }}
          >
            {start > 0 ? (
              <button type="button" className="btn btn-ghost btn-sm chat-earlier" onClick={showEarlier}>
                Show earlier messages ({start})
              </button>
            ) : null}
            {items.length === 0 && !pending.length ? (
              <EmptyState title="Brief Desk">Say what you want done. Desk plans it, splits it into threads, and reports back.</EmptyState>
            ) : null}
            {(start ? items.slice(start) : items).map((item) => (
              <div key={item.id} id={chatDomId(item.id)} className="chat-item">
                <ChatRow
                  item={item}
                  projectId={projectId}
                  attentionIds={attentionIds}
                  answering={answering}
                  onAnswer={onAnswer}
                  onOwnWords={onOwnWords}
                  view={views.get(item.id)}
                  now={ticks(views.get(item.id)) ? now : undefined}
                  onJump={onJump}
                  deskId={s.chat.agentId}
                  onPair={onPair}
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
          <Composer projectId={projectId} draft={draft} setDraft={setDraft} textareaRef={textareaRef} onSent={(t) => setPending((p) => [...p, t])} attachments={attachments} />
        </section>
        <div className="conv-side">
          <PlanPanel project={project} proxyDown={proxy === 'down'} attention={projectAttention} />
          {narrow ? <ServicesCard project={project} /> : null}
        </div>
      </div>
      {pairOf ? <PairSheet projectId={projectId} messages={s.messages} a={pairOf[0]} b={pairOf[1]} onClose={() => setPairOf(null)} /> : null}
    </div>
  );
}
