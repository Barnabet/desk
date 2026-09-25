/** A skill as the Skills screen names it: a global skill, or one of a project's. */
export type SkillRef = { scope: 'global' | 'project'; projectId?: string; name: string };

export const skillKey = (r: SkillRef) => (r.scope === 'global' ? `global:${r.name}` : `project:${r.projectId}:${r.name}`);

export function parseSkillKey(key: string): SkillRef | null {
  const parts = key.split(':');
  if (parts[0] === 'global' && parts.length === 2 && parts[1]) return { scope: 'global', name: parts[1] };
  if (parts[0] === 'project' && parts.length === 3 && parts[1] && parts[2]) return { scope: 'project', projectId: parts[1], name: parts[2] };
  return null;
}
