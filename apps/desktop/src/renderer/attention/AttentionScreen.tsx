import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AttentionItem } from '@desk/protocol';
import { call, DeskCallError } from '../bridge';
import { EmptyState } from '../components/EmptyState';
import { toast, toastError } from '../components/Toast';
import { plural } from '../format';
import { href, navigate, replaceRoute } from '../router';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { Inspector } from './Inspector';
import { StripRack } from './StripRack';
import { rackOrder, STRIP_CODE, waited } from './strips';
import './attention.css';

const WHO: Record<string, string> = { user: 'you, in another window', desk: 'Desk', system: 'Desk (the thread was stopped)' };

function openTarget(i: AttentionItem): string {
  if ((i.kind === 'stalled' || i.kind === 'failed' || i.kind === 'approval') && i.ref.thread_id) return href({ name: 'project', id: i.project_id, tab: 'threads', threadId: i.ref.thread_id });
  return href({ name: 'project', id: i.project_id, tab: 'conversation' });
}

const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);

/** Everything that needs you, as flight strips in a rack, with the selected strip inspected on the right. */
export function AttentionScreen({ itemId }: { itemId?: string }) {
  const items = useGlobal((g) => g.attention);
  const overview = useGlobal((g) => g.overview);
  const now = useNow();
  const { bays, flat } = useMemo(() => rackOrder(items), [items]);
  const titles = useMemo(() => new Map(overview.flatMap((p) => p.threads.map((t) => [t.id, t.title] as const))), [overview]);
  const threadTitle = useCallback((id: string) => titles.get(id) ?? null, [titles]);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [answered, setAnswered] = useState<Set<string>>(new Set());
  const lastIndex = useRef(0);

  const found = flat.findIndex((i) => i.id === itemId);
  const index = found >= 0 ? found : Math.min(lastIndex.current, flat.length - 1);
  const selected = index >= 0 ? flat[index] : undefined;
  useEffect(() => {
    if (found >= 0) lastIndex.current = found;
    else if (selected) replaceRoute({ name: 'attention', item: selected.id });
  }, [found, selected]);
  useEffect(() => {
    setNote('');
    setBusy(null);
  }, [selected?.id]);

  const select = useCallback((id: string) => replaceRoute({ name: 'attention', item: id }), []);

  const resolve = useCallback(
    async (decision: 'approved' | 'denied') => {
      if (!selected || selected.kind !== 'approval' || !selected.ref.approval_id || busy) return;
      setBusy(decision);
      try {
        await call('approvals.resolve', { id: selected.ref.approval_id, decision, ...(note.trim() ? { note: note.trim() } : {}) });
      } catch (err) {
        if (err instanceof DeskCallError && err.status === 409) {
          const all = await call('approvals.list', { projectId: selected.project_id }).catch(() => []);
          const by = all.find((x) => x.id === selected.ref.approval_id)?.resolved_by;
          toast({ tone: 'info', message: `Already decided by ${by ? (WHO[by] ?? by) : 'someone else'}.` });
        } else toastError(err);
        setBusy(null);
      }
    },
    [selected, note, busy],
  );
  const answer = useCallback(
    async (text: string) => {
      if (!selected) return;
      setBusy(text);
      try {
        await call('projects.send', { id: selected.project_id, text });
        setAnswered((s) => new Set(s).add(selected.id));
      } catch (err) {
        toastError(err);
      } finally {
        setBusy(null);
      }
    },
    [selected],
  );
  const dismiss = useCallback(async () => {
    if (!selected) return;
    setBusy('dismiss');
    try {
      await call('attention.dismiss', { id: selected.id });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  }, [selected]);
  const open = useCallback(() => selected && navigate(openTarget(selected)), [selected]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!flat.length) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === 'Enter') {
        e.preventDefault();
        void resolve('approved');
      } else if (mod && e.key === 'Backspace') {
        e.preventDefault();
        void resolve('denied');
      } else if (!mod && !e.altKey && !typing(e.target)) {
        const k = e.key.toLowerCase();
        if (k === 'j' || k === 'k') {
          e.preventDefault();
          const next = Math.max(0, Math.min(flat.length - 1, index + (k === 'j' ? 1 : -1)));
          select(flat[next]!.id);
        } else if (k === 'e') {
          e.preventDefault();
          open();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [flat, index, resolve, open, select]);

  const projects = new Set(items.map((i) => i.project_id)).size;
  const oldest = flat.reduce<string | null>((m, i) => (m === null || i.created_at < m ? i.created_at : m), null);

  return (
    <div className="attention">
      <div className="attention-left">
        <header className="attention-head">
          <h1>Needs you</h1>
          <p className="muted">
            {flat.length ? `${plural(flat.length, 'item')} across ${plural(projects, 'project')} · oldest ${waited(oldest!, now)}` : 'Nothing is waiting on you.'}
          </p>
        </header>
        <StripRack bays={bays} now={now} selectedId={selected?.id ?? null} threadTitle={threadTitle} onSelect={select} />
        <p className="keys mono">J K move · ⌘⏎ approve · ⌘⌫ deny · E open</p>
        <div className="strip-legend" aria-hidden="true">
          <span>
            <span className="strip-code-badge code-approval">{STRIP_CODE.approval}</span>Approval
          </span>
          <span>
            <span className="strip-code-badge code-question">{STRIP_CODE.question}</span>Question from Desk
          </span>
          <span>
            <span className="strip-code-badge code-needs_you">{STRIP_CODE.needs_you}</span>From a report
          </span>
          <span>
            <span className="strip-code-badge code-stalled">{STRIP_CODE.stalled}</span>Stalled thread
          </span>
          <span>
            <span className="strip-code-badge code-failed">{STRIP_CODE.failed}</span>Failed thread
          </span>
          <span className="muted">Bar = wait, 0–2h</span>
        </div>
      </div>
      <div className="attention-right">
        {selected ? (
          <Inspector
            key={selected.id}
            item={selected}
            index={index}
            total={flat.length}
            now={now}
            a={{ note, setNote, busy, answered: answered.has(selected.id), resolve: (d) => void resolve(d), answer: (t) => void answer(t), dismiss: () => void dismiss(), open }}
          />
        ) : (
          <EmptyState title="All clear" action={<a href="#/map">Back to the map</a>}>
            Approvals, questions, hand-offs from reports and stuck threads land here.
          </EmptyState>
        )}
      </div>
    </div>
  );
}
