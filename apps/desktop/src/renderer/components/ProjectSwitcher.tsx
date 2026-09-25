import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import type { ProjectSummary } from '@desk/protocol';
import { href } from '@desk/ui-core';
import { projectSummaryLine } from '../map/OrbitMap';
import { navigate } from '../router';
import { useGlobal } from '../state/global';
import { unreadIn, useSeen } from '../state/unread';

/** Filters by name or goal, then orders: needs you, unread, most recent activity. */
export function switcherList(projects: ProjectSummary[], query: string, seen: Record<string, string>): ProjectSummary[] {
  const q = query.trim().toLowerCase();
  return projects
    .filter((p) => !q || p.project.name.toLowerCase().includes(q) || p.project.goal.toLowerCase().includes(q))
    .sort((a, b) => Number(b.attention_count > 0) - Number(a.attention_count > 0) || Number(unreadIn(b, seen)) - Number(unreadIn(a, seen)) || b.project.updated_at.localeCompare(a.project.updated_at));
}

/** How long the list stays open after the pointer leaves it (moving from the arrow into the list). */
const HOVER_CLOSE_MS = 250;

/**
 * The project switcher in the title bar: "Projects ▾" with no project, or just the ▾ next to the project's name.
 * Hovering the trigger opens it (it closes when the pointer leaves); clicking or ⌘P opens it with the search focused.
 */
export function ProjectSwitcher({ currentId }: { currentId: string | null }) {
  const overview = useGlobal((g) => g.overview);
  const seen = useSeen();
  const [open, setOpen] = useState(false);
  /** Opened by hovering (closes on leave) rather than by click or ⌘P (closes on outside click or Escape). */
  const [byHover, setByHover] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const list = useMemo(() => switcherList(overview, query, seen), [overview, query, seen]);
  const othersUnread = overview.some((p) => p.project.id !== currentId && unreadIn(p, seen));

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'p') {
        e.preventDefault();
        setByHover(false);
        setOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActive(0);
    if (!byHover) inputRef.current?.focus();
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [open]);
  useEffect(() => setActive(0), [query]);
  useEffect(() => () => clearTimeout(closeTimer.current), []);
  const hoverOpen = () => {
    clearTimeout(closeTimer.current);
    if (!open) {
      setByHover(true);
      setOpen(true);
    }
  };
  const hoverLeave = () => {
    if (!byHover) return;
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => setOpen(false), HOVER_CLOSE_MS);
  };

  const go = (p: ProjectSummary | undefined) => {
    setOpen(false);
    navigate(p ? href({ name: 'project', id: p.project.id, tab: 'conversation' }) : href({ name: 'map', newProject: true }));
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    const n = list.length + 1;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => (a + 1) % n);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => (a - 1 + n) % n);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      go(list[active]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    }
  };
  const optId = (i: number) => `${listId}-${i}`;

  return (
    <div className="switcher" ref={rootRef} onMouseLeave={hoverLeave} onMouseEnter={() => open && clearTimeout(closeTimer.current)}>
      <button
        type="button"
        className="switcher-btn"
        aria-label="Switch project (⌘P)"
        aria-haspopup="dialog"
        aria-expanded={open}
        onMouseEnter={hoverOpen}
        onClick={() => {
          clearTimeout(closeTimer.current);
          if (open && byHover) {
            // A click on a list opened by hovering keeps it open and moves focus into the search.
            setByHover(false);
            inputRef.current?.focus();
            return;
          }
          setByHover(false);
          setOpen((o) => !o);
        }}
      >
        {currentId ? null : <span>Projects</span>}
        <span aria-hidden="true">▾</span>
        {othersUnread ? <span className="unread-dot" aria-label="Another project has news" /> : null}
      </button>
      {open ? (
        <div className="switcher-pop card" role="dialog" aria-label="Switch project">
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={optId(active)}
            aria-label="Find a project"
            placeholder="Find a project"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKey}
          />
          <ul id={listId} role="listbox" aria-label="Projects">
            {list.map((p, i) => (
              <li
                key={p.project.id}
                id={optId(i)}
                role="option"
                aria-selected={i === active}
                className={p.project.id === currentId ? 'current' : undefined}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(p)}
              >
                <span className="switcher-name">
                  {p.project.name}
                  {unreadIn(p, seen) ? <span className="unread-dot" aria-label="unread" /> : null}
                </span>
                <span className="switcher-sub">{projectSummaryLine(p)}</span>
                {p.attention_count ? <span className="switcher-needs">{p.attention_count}</span> : null}
              </li>
            ))}
            <li id={optId(list.length)} role="option" aria-selected={active === list.length} className="switcher-new" onMouseEnter={() => setActive(list.length)} onClick={() => go(undefined)}>
              + New project
            </li>
          </ul>
        </div>
      ) : null}
    </div>
  );
}
