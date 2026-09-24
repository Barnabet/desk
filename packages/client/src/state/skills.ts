import type { AgentStatus } from '@desk/protocol';
import type { SkillSummary } from '../types';

export type SkillUse = { threadId: string; title: string | null; status: AgentStatus; projectId: string };
export type SkillNode = {
  key: string;
  name: string;
  scope: 'global' | 'project';
  projectId: string | null;
  projectName: string | null;
  version: number;
  description: string;
  error?: string;
  /** For a global skill: projects that have their own skill of the same name. */
  shadowedIn: string[];
  /** For a project skill: whether a global skill of the same name exists (and is shadowed here). */
  shadows: boolean;
  usedBy: SkillUse[];
};

/**
 * Nodes for the skills map. `projects[].skills` is the project's resolved list (as returned by
 * GET /projects/:id/skills); only its project-scope entries become project nodes.
 */
export function buildSkillGraph(input: {
  global: SkillSummary[];
  projects: Array<{ id: string; name: string; skills: SkillSummary[] }>;
  threads: Array<{ id: string; title: string | null; status: AgentStatus; projectId: string; skills: string[] }>;
}): SkillNode[] {
  const globals = new Map(input.global.map((g) => [g.name, g]));
  const nodes = new Map<string, SkillNode>();
  const base = (s: SkillSummary) => ({ name: s.name, version: s.version, description: s.description, ...(s.error ? { error: s.error } : {}), usedBy: [] as SkillUse[] });
  for (const g of input.global) nodes.set(`global:${g.name}`, { key: `global:${g.name}`, scope: 'global', projectId: null, projectName: null, shadowedIn: [], shadows: false, ...base(g) });
  const projectSkills = new Map<string, Set<string>>();
  for (const p of input.projects) {
    const own = p.skills.filter((s) => s.scope === 'project');
    projectSkills.set(p.id, new Set(own.map((s) => s.name)));
    for (const s of own) {
      const key = `project:${p.id}:${s.name}`;
      nodes.set(key, { key, scope: 'project', projectId: p.id, projectName: p.name, shadowedIn: [], shadows: globals.has(s.name), ...base(s) });
      nodes.get(`global:${s.name}`)?.shadowedIn.push(p.id);
    }
  }
  for (const t of input.threads) {
    for (const name of t.skills) {
      const key = projectSkills.get(t.projectId)?.has(name) ? `project:${t.projectId}:${name}` : `global:${name}`;
      nodes.get(key)?.usedBy.push({ threadId: t.id, title: t.title, status: t.status, projectId: t.projectId });
    }
  }
  return [...nodes.values()];
}
