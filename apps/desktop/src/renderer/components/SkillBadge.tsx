export function SkillBadge({ name, scope }: { name: string; scope?: 'global' | 'project' }) {
  return (
    <span className="skill-badge" {...(scope ? { title: `${scope} skill` } : {})}>
      {name}
    </span>
  );
}
