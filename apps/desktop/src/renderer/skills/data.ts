import { useCallback, useEffect, useMemo, useState } from 'react';
import { buildSkillGraph, type SkillNode, type SkillSummary } from '@desk/client';
import { call } from '../bridge';
import { describeError } from '../components/Toast';
import { useGlobal } from '../state/global';

export type SkillRef = { scope: 'global' | 'project'; projectId?: string; name: string };

export const skillKey = (r: SkillRef) => (r.scope === 'global' ? `global:${r.name}` : `project:${r.projectId}:${r.name}`);

export function parseSkillKey(key: string): SkillRef | null {
  const parts = key.split(':');
  if (parts[0] === 'global' && parts.length === 2 && parts[1]) return { scope: 'global', name: parts[1] };
  if (parts[0] === 'project' && parts.length === 3 && parts[1] && parts[2]) return { scope: 'project', projectId: parts[1], name: parts[2] };
  return null;
}

/** The IPC scope argument: project skills name their project; global ones don't. */
export const scopeArg = (r: SkillRef): { projectId?: string } => (r.scope === 'project' && r.projectId ? { projectId: r.projectId } : {});

type Lists = { global: SkillSummary[]; projects: Record<string, SkillSummary[]> };

/** Global and per-project skills, joined with live thread usage from the overview. Refetches on focus and every 30 s. */
export function useSkills(): { status: 'loading' | 'ready' | 'error'; error: string | null; nodes: SkillNode[]; refresh(): Promise<void> } {
  const overview = useGlobal((g) => g.overview);
  const [lists, setLists] = useState<Lists | null>(null);
  const [error, setError] = useState<string | null>(null);
  const projectIds = overview.map((p) => p.project.id).join(',');

  const refresh = useCallback(async () => {
    try {
      const ids = projectIds ? projectIds.split(',') : [];
      const [global, ...perProject] = await Promise.all([call('skills.list', {}), ...ids.map((id) => call('skills.list', { projectId: id }).catch(() => [] as SkillSummary[]))]);
      setLists({ global, projects: Object.fromEntries(ids.map((id, i) => [id, perProject[i] ?? []])) });
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, [projectIds]);

  useEffect(() => {
    void refresh();
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    const t = setInterval(() => void refresh(), 30_000);
    return () => {
      window.removeEventListener('focus', onFocus);
      clearInterval(t);
    };
  }, [refresh]);

  const nodes = useMemo(
    () =>
      lists
        ? buildSkillGraph({
            global: lists.global,
            projects: overview.map((p) => ({ id: p.project.id, name: p.project.name, skills: lists.projects[p.project.id] ?? [] })),
            threads: overview.flatMap((p) => p.threads.map((t) => ({ id: t.id, title: t.title, status: t.status, projectId: p.project.id, skills: t.skills }))),
          })
        : [],
    [lists, overview],
  );
  return { status: lists ? 'ready' : error ? 'error' : 'loading', error, nodes, refresh };
}

/** `user` → you, `agent:<id>` → the agent's title when known, `catalog:` and `builtin:` origins → where the skill came from. */
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
