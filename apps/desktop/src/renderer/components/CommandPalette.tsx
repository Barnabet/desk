import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import type { SkillSummary } from '@desk/client';
import { clip, type ArtifactKind, type CatalogItem } from '@desk/protocol';
import { call } from '../bridge';
import { GROUP_ORDER, rankPalette, type PaletteItem } from '../palette';
import { href, navigate } from '../router';
import { useGlobal } from '../state/global';

type Loaded = {
  skills: Array<{ key: string; name: string; scope: string; description: string; project: string | null }>;
  library: Array<{ projectId: string; project: string; path: string; title: string; kind: ArtifactKind }>;
  catalog: CatalogItem[];
};
const MAX_MEMORY_PROJECTS = 8;

const GO: PaletteItem[] = [
  { id: 'go:map', group: 'Go to', title: 'Map', detail: 'All projects', route: href({ name: 'map' }) },
  { id: 'go:attention', group: 'Go to', title: 'Needs you', detail: 'Approvals, questions and hand-offs', keywords: 'attention approvals', route: href({ name: 'attention' }) },
  { id: 'go:skills', group: 'Go to', title: 'Skills', route: href({ name: 'skills' }) },
  { id: 'go:catalog', group: 'Go to', title: 'Skill catalog', detail: 'Install reviewed skills', keywords: 'install add', route: href({ name: 'catalog' }) },
  { id: 'go:system', group: 'Go to', title: 'System', detail: 'deskd, models, usage', keywords: 'settings daemon endpoint', route: href({ name: 'system' }) },
  { id: 'go:new', group: 'Go to', title: 'New project', keywords: 'create', route: href({ name: 'map', newProject: true }) },
];

/** ⌘K: jump to a place, project, thread, skill or library file, or search every project's memory. */
export function CommandPalette() {
  const overview = useGlobal((g) => g.overview);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [memory, setMemory] = useState<PaletteItem[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
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
    setMemory([]);
    inputRef.current?.focus();
    let live = true;
    const projects = overview.map((p) => ({ id: p.project.id, name: p.project.name }));
    void Promise.all([
      call('skills.list', {}).catch(() => [] as SkillSummary[]),
      Promise.all(projects.map((p) => call('skills.list', { projectId: p.id }).catch(() => [] as SkillSummary[]))),
      Promise.all(projects.map((p) => call('library.list', { projectId: p.id }).catch(() => []))),
      call('catalog.list', {}).catch(() => [] as CatalogItem[]),
    ]).then(([global, perProject, libraries, catalog]) => {
      if (!live) return;
      const skills: Loaded['skills'] = global.map((s) => ({ key: `global:${s.name}`, name: s.name, scope: 'global', description: s.description, project: null }));
      perProject.forEach((list, i) => {
        for (const s of list) if (s.scope === 'project') skills.push({ key: `project:${projects[i]!.id}:${s.name}`, name: s.name, scope: 'project', description: s.description, project: projects[i]!.name });
      });
      const library = libraries.flatMap((list, i) => list.map((a) => ({ projectId: projects[i]!.id, project: projects[i]!.name, path: a.path, title: a.title, kind: a.kind })));
      setLoaded({ skills, library, catalog });
    });
    return () => {
      live = false;
    };
    // Loaded once per opening; the overview at that moment is enough.
  }, [open]);

  useEffect(() => {
    const q = query.trim();
    if (!open || q.length < 3) {
      setMemory([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      const projects = overview.slice(0, MAX_MEMORY_PROJECTS);
      void Promise.all(projects.map((p) => call('memory.list', { projectId: p.project.id, q }).catch(() => []))).then((results) => {
        if (!live) return;
        setMemory(
          results.flatMap((rows, i) =>
            rows.slice(0, 2).map((m) => ({
              id: `memory:${m.id}`,
              group: 'Memory' as const,
              title: clip(m.content, 90),
              detail: `${projects[i]!.project.name} · ${m.kind}`,
              route: href({ name: 'project', id: projects[i]!.project.id, tab: 'memory', q }),
            })),
          ),
        );
      });
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [open, query, overview]);

  const items = useMemo(() => {
    const all: PaletteItem[] = [...GO];
    for (const p of overview) {
      all.push({ id: `project:${p.project.id}`, group: 'Projects', title: p.project.name, detail: p.project.goal, route: href({ name: 'project', id: p.project.id, tab: 'conversation' }) });
      for (const t of p.threads)
        all.push({ id: `thread:${t.id}`, group: 'Threads', title: t.title ?? 'Untitled thread', detail: `${p.project.name} · ${t.status}`, route: href({ name: 'project', id: p.project.id, tab: 'threads', threadId: t.id }) });
    }
    for (const s of loaded?.skills ?? []) all.push({ id: `skill:${s.key}`, group: 'Skills', title: s.name, detail: `${s.project ?? 'Global'} · ${s.description}`, route: href({ name: 'skills', skill: s.key }) });
    for (const c of loaded?.catalog ?? []) {
      const global = c.installs.find((i) => i.scope === 'global');
      if (global?.state === 'installed' || global?.state === 'name_taken') continue;
      const verb = global?.state === 'update_available' ? 'Update' : 'Install';
      all.push({ id: `catalog:${c.id}`, group: 'Catalog', title: `${verb} ${c.title}`, detail: c.summary, keywords: `${c.id} ${c.category}`, route: href({ name: 'catalog', review: c.id }) });
    }
    for (const a of loaded?.library ?? [])
      all.push({ id: `lib:${a.projectId}:${a.path}`, group: 'Library', title: a.title, detail: `${a.project} · ${a.path}`, keywords: a.kind, route: href({ name: 'project', id: a.projectId, tab: 'library', file: a.path }) });
    return [...all, ...memory];
  }, [overview, loaded, memory]);
  const shown = useMemo(() => rankPalette(items, query), [items, query]);
  useEffect(() => setActive(0), [query]);

  if (!open) return null;
  const go = (i: PaletteItem | undefined) => {
    if (!i) return;
    setOpen(false);
    navigate(i.route);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(shown.length - 1, a + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      go(shown[active]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    }
  };
  const optId = (i: number) => `${listId}-${i}`;
  let index = -1;
  return (
    <div className="palette-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}>
      <div className="palette card" role="dialog" aria-modal="true" aria-label="Search Desk">
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={shown.length ? optId(active) : undefined}
          aria-label="Search projects, threads, skills, files and memory"
          placeholder="Search projects, threads, skills, files and memory"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKey}
        />
        <ul id={listId} role="listbox" aria-label="Results">
          {GROUP_ORDER.map((g) => {
            const inGroup = shown.filter((i) => i.group === g);
            if (!inGroup.length) return null;
            return [
              <li key={`h-${g}`} role="presentation" className="palette-group">
                {g}
              </li>,
              ...inGroup.map((i) => {
                index += 1;
                const n = index;
                return (
                  <li key={i.id} id={optId(n)} role="option" aria-selected={n === active} onMouseEnter={() => setActive(n)} onClick={() => go(i)}>
                    <span className="palette-title">{i.title}</span>
                    {i.detail ? <span className="palette-detail">{i.detail}</span> : null}
                  </li>
                );
              }),
            ];
          })}
          {!shown.length ? (
            <li role="presentation" className="palette-empty">
              {query.trim().length >= 3 ? 'Nothing found.' : 'Keep typing…'}
            </li>
          ) : null}
        </ul>
        <div className="palette-foot muted small">↑↓ move · ⏎ open · esc close{query.trim().length >= 3 ? ' · memory is searched in every project' : ''}</div>
      </div>
    </div>
  );
}
