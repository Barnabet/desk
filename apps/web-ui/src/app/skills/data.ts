import { computed, effect, inject, signal, untracked, type Signal } from '@angular/core';
import { buildSkillGraph, type SkillNode, type SkillSummary } from '@desk/client';
import type { SkillRef } from '@desk/ui-core';
import { describeError } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';

/** The IPC scope argument: project skills name their project; global ones don't. */
export const scopeArg = (r: SkillRef): { projectId?: string } => (r.scope === 'project' && r.projectId ? { projectId: r.projectId } : {});

/**
 * `user` → you, `agent:<id>` → the agent's title when known, `builtin:<name>` → Desk itself (a copy of a built-in skill),
 * `catalog:<id>@<commit>` → the catalog with its commit.
 */
export function whoLabel(origin: string | null, titles: Map<string, string>): string {
  if (!origin) return 'Unknown';
  if (origin === 'user') return 'You';
  if (origin.startsWith('agent:')) return titles.get(origin.slice(6)) ?? 'Desk';
  if (origin.startsWith('builtin:')) return 'Built into Desk';
  if (origin.startsWith('catalog:')) {
    const marker = origin.slice(origin.lastIndexOf('@') + 1);
    return /^[0-9a-f]{40}$/.test(marker) ? `Catalog · ${marker.slice(0, 7)}` : 'Catalog';
  }
  return origin;
}

/** The global list, and each project's own keyed by project id (a Map, so no id reads a prototype member). */
type Lists = { global: SkillSummary[]; projects: Map<string, SkillSummary[]> };

/** What `injectSkills` gives a screen (the React `useSkills` result). */
export type SkillsState = {
  status: Signal<'loading' | 'ready' | 'error'>;
  error: Signal<string | null>;
  nodes: Signal<SkillNode[]>;
  refresh(): Promise<void>;
};

/**
 * Global and per-project skills, joined with live thread usage from the overview (renderer/skills/data.ts `useSkills`).
 * Lists again when the projects change, on window focus and every 30 s. Call it in a field initializer.
 */
export function injectSkills(): SkillsState {
  const bridge = inject(DeskBridge);
  const global = inject(GlobalStore);
  const overview = computed(() => global.state().overview);
  const projectIds = computed(() => overview().map((p) => p.project.id).join(','));
  const lists = signal<Lists | null>(null);
  const error = signal<string | null>(null);
  // Refreshes overlap (focus, the timer, a new project); only the latest one's answer lands, so a slow earlier answer
  // never overwrites a newer list.
  let latest = 0;

  const refresh = async (): Promise<void> => {
    const run = ++latest;
    const joined = untracked(projectIds);
    const ids = joined ? joined.split(',') : [];
    try {
      const [all, ...perProject] = await Promise.all([
        bridge.call('skills.list', {}),
        ...ids.map((id) => bridge.call('skills.list', { projectId: id }).catch((): SkillSummary[] => [])),
      ]);
      if (run !== latest) return;
      lists.set({ global: all ?? [], projects: new Map(ids.map((id, i) => [id, perProject[i] ?? []])) });
      error.set(null);
    } catch (err) {
      if (run === latest) error.set(describeError(err).message);
    }
  };

  // React's [refresh] dependency is the joined project ids: a new project, or one gone, lists again.
  effect((onCleanup) => {
    projectIds();
    untracked(() => void refresh());
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    const timer = setInterval(() => void refresh(), 30_000);
    onCleanup(() => {
      window.removeEventListener('focus', onFocus);
      clearInterval(timer);
    });
  });

  const nodes = computed(() => {
    const l = lists();
    if (!l) return [];
    const projects = overview();
    return buildSkillGraph({
      global: l.global,
      projects: projects.map((p) => ({ id: p.project.id, name: p.project.name, skills: l.projects.get(p.project.id) ?? [] })),
      threads: projects.flatMap((p) => p.threads.map((t) => ({ id: t.id, title: t.title, status: t.status, projectId: p.project.id, skills: t.skills }))),
    });
  });
  const status = computed<'loading' | 'ready' | 'error'>(() => (lists() ? 'ready' : error() ? 'error' : 'loading'));
  return { status, error: error.asReadonly(), nodes, refresh };
}
