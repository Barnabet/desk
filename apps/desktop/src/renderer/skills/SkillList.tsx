import type { SkillNode } from '@desk/client';

/** The list alternative to the map: global skills, then each project's own. */
export function SkillList(o: { nodes: SkillNode[]; projectNames: Map<string, string>; selected: string | null; onSelect(key: string): void }) {
  const groups: Array<{ title: string; nodes: SkillNode[] }> = [{ title: 'Global', nodes: o.nodes.filter((n) => n.scope === 'global') }];
  for (const [id, name] of o.projectNames) {
    const own = o.nodes.filter((n) => n.scope === 'project' && n.projectId === id);
    if (own.length) groups.push({ title: name, nodes: own });
  }
  return (
    <div className="skill-list">
      {groups.map((g) => (
        <section key={g.title} aria-label={`${g.title} skills`}>
          <h2 className="skill-list-title">{g.title}</h2>
          {g.nodes.length ? (
            <ul>
              {g.nodes
                .slice()
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((n) => (
                  <li key={n.key}>
                    <button type="button" className={`skill-row${o.selected === n.key ? ' current' : ''}`} aria-pressed={o.selected === n.key} onClick={() => o.onSelect(n.key)}>
                      <span className="mono skill-row-name">{n.name}</span>
                      <span className="muted small grow">{n.error ? `Broken: ${n.error}` : n.description}</span>
                      {n.shadows ? <span className="chip chip-wait">shadows global</span> : null}
                      {n.shadowedIn.length ? <span className="chip chip-idle">shadowed in {n.shadowedIn.length}</span> : null}
                      {n.usedBy.length ? <span className="chip chip-run">in use · {n.usedBy.length}</span> : null}
                      <span className="mono small muted">v{n.version}</span>
                    </button>
                  </li>
                ))}
            </ul>
          ) : (
            <p className="muted small">No global skills yet.</p>
          )}
        </section>
      ))}
    </div>
  );
}
