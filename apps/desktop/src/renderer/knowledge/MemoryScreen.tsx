import { useEffect, useMemo, useState } from 'react';
import type { MemoryKind } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { toastError } from '../components/Toast';
import { clock, plural } from '../format';
import { href, replaceRoute } from '../router';
import { useSession } from '../state/session';
import { originAgent } from './library';
import { activeEntries, chainOf, MEMORY_KINDS, memoryFromEvents, type MemoryEntry } from './memory';
import './knowledge.css';

function Source({ projectId, source, label }: { projectId: string; source: string; label(id: string): string }) {
  const agent = originAgent(source);
  return agent ? <a href={href({ name: 'project', id: projectId, tab: 'threads', threadId: agent })}>{label(agent)}</a> : <>you</>;
}

function Entry(o: { projectId: string; m: MemoryEntry; chain: MemoryEntry[]; label(id: string): string }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(o.m.content);
  const [busy, setBusy] = useState<string | null>(null);
  const [showChain, setShowChain] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const save = async () => {
    if (!draft.trim() || draft.trim() === o.m.content) return setEditing(false);
    setBusy('save');
    try {
      await call('memory.correct', { projectId: o.projectId, memoryId: o.m.id, update: { content: draft.trim() } });
      setEditing(false);
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(null);
    }
  };
  const remove = async () => {
    setConfirm(false);
    setBusy('delete');
    try {
      await call('memory.remove', { projectId: o.projectId, memoryId: o.m.id });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  };
  return (
    <li className="memory-entry card" aria-label={`${o.m.kind}: ${o.m.content.slice(0, 80)}`}>
      {editing ? (
        <div className="field">
          <label htmlFor={`mem-${o.m.id}`}>Correct this entry</label>
          <textarea id={`mem-${o.m.id}`} className="textarea" rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="actions">
            <Button size="sm" variant="primary" pending={busy === 'save'} onClick={() => void save()}>
              Save correction
            </Button>
            <Button size="sm" variant="ghost" onClick={() => (setEditing(false), setDraft(o.m.content))}>
              Cancel
            </Button>
            <span className="muted small">The old version is kept in the history.</span>
          </div>
        </div>
      ) : (
        <SafeMarkdown className="memory-content" text={o.m.content} />
      )}
      <div className="memory-meta small muted">
        <span>
          <Source projectId={o.projectId} source={o.m.source} label={o.label} /> · {clock(o.m.ts)}
        </span>
        {o.chain.length ? (
          <button type="button" className="link small" aria-expanded={showChain} onClick={() => setShowChain((v) => !v)}>
            Corrected {plural(o.chain.length, 'time')}
          </button>
        ) : null}
        <span className="grow" />
        {editing ? null : (
          <>
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              Correct
            </Button>
            <Button size="sm" variant="ghost" pending={busy === 'delete'} onClick={() => setConfirm(true)}>
              Delete
            </Button>
          </>
        )}
      </div>
      {showChain ? (
        <ol className="memory-chain" aria-label="Earlier versions">
          {o.chain.map((c) => (
            <li key={c.id}>
              <span className="memory-old">{c.content}</span>
              <span className="muted small">
                {' '}
                · <Source projectId={o.projectId} source={c.source} label={o.label} /> · {clock(c.ts)}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
      {confirm ? (
        <ConfirmDialog title="Delete this memory?" confirmLabel="Delete" danger onConfirm={() => void remove()} onCancel={() => setConfirm(false)}>
          Desk and its threads stop seeing it. To change it instead, use Correct.
        </ConfirmDialog>
      ) : null}
    </li>
  );
}

/** What Desk remembers for this project: grouped by kind, searchable, and correctable by you. */
export function MemoryScreen({ projectId, q }: { projectId: string; q?: string }) {
  const s = useSession(projectId);
  const all = useMemo(() => memoryFromEvents(s.events), [s.events]);
  const active = useMemo(() => activeEntries(all), [all]);
  const [query, setQuery] = useState(q ?? '');
  const [hits, setHits] = useState<string[] | null>(null);
  const [kind, setKind] = useState<MemoryKind>('fact');
  const [content, setContent] = useState('');
  const [adding, setAdding] = useState(false);
  const label = (id: string) => s.project?.threads.find((t) => t.id === id)?.title ?? (s.project?.desk?.id === id ? 'Desk' : 'a thread');

  useEffect(() => setQuery(q ?? ''), [q]);
  useEffect(() => {
    const term = query.trim();
    if (!term) {
      setHits(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      call('memory.list', { projectId, q: term })
        .then((rows) => live && setHits(rows.map((r) => r.id)))
        .catch((err) => live && (toastError(err), setHits([])));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [projectId, query, active.length]);

  const add = async () => {
    if (!content.trim()) return;
    setAdding(true);
    try {
      await call('memory.add', { projectId, entry: { kind, content: content.trim() } });
      setContent('');
    } catch (err) {
      toastError(err);
    } finally {
      setAdding(false);
    }
  };

  if (s.status === 'loading') return <div className="page muted">Loading…</div>;
  if (s.status !== 'ready')
    return (
      <div className="page">
        <EmptyState title="Couldn't load this project">{s.error}</EmptyState>
      </div>
    );

  const byId = new Map(active.map((m) => [m.id, m]));
  const shown = hits ? hits.map((id) => byId.get(id)).filter((m): m is MemoryEntry => !!m) : active;
  return (
    <div className="page memory">
      <div className="knowledge-head">
        <h1 className="title">Memory</h1>
        <span className="muted">{plural(active.length, 'entry', 'entries')}. Desk and its threads read these before they work.</span>
      </div>
      <form
        className="card memory-add"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <label htmlFor="memory-new">Remember something</label>
        <div className="memory-add-row">
          <select aria-label="Kind" className="select" value={kind} onChange={(e) => setKind(e.target.value as MemoryKind)}>
            {MEMORY_KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.kind}
              </option>
            ))}
          </select>
          <input id="memory-new" type="text" value={content} onChange={(e) => setContent(e.target.value)} placeholder="e.g. We never discount annual plans" />
          <Button type="submit" variant="primary" pending={adding} disabled={!content.trim()}>
            Add
          </Button>
        </div>
      </form>
      <input
        className="knowledge-search"
        type="search"
        aria-label="Search memory"
        placeholder="Search memory"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          replaceRoute({ name: 'project', id: projectId, tab: 'memory', ...(e.target.value.trim() ? { q: e.target.value } : {}) });
        }}
      />
      {hits && !shown.length ? <p className="muted">No entries match “{query.trim()}”.</p> : null}
      {!active.length ? <EmptyState title="Nothing remembered yet">Desk writes down decisions, facts and preferences as it works. You can add and correct them here.</EmptyState> : null}
      {hits ? (
        <ul className="memory-list" aria-label="Search results">
          {shown.map((m) => (
            <Entry key={m.id} projectId={projectId} m={m} chain={chainOf(all, m.id)} label={label} />
          ))}
        </ul>
      ) : (
        MEMORY_KINDS.map((k) => {
          const list = shown.filter((m) => m.kind === k.kind);
          return list.length ? (
            <section key={k.kind} aria-labelledby={`mem-group-${k.kind}`}>
              <h2 id={`mem-group-${k.kind}`} className="memory-group">
                {k.title} <span className="muted">{list.length}</span>
              </h2>
              <ul className="memory-list">
                {list.map((m) => (
                  <Entry key={m.id} projectId={projectId} m={m} chain={chainOf(all, m.id)} label={label} />
                ))}
              </ul>
            </section>
          ) : null;
        })
      )}
    </div>
  );
}
