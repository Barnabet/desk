import type { SkillNode } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';
import { href, layoutSkillsMap, type MapTone } from '@desk/ui-core';
import { MapCanvas } from '../map/MapCanvas';
import '../map/map.css';

const TERRITORY: Record<MapTone, { fill: string; stroke: string; text: string }> = {
  running: { fill: '#E3E8F5', stroke: '#C9D3EC', text: '#1F45A8' },
  waiting: { fill: '#F3E6CF', stroke: '#E6D3AF', text: '#7A4500' },
  idle: { fill: '#E6E1D7', stroke: '#D6CFC1', text: '#4A4740' },
};
const LINE: Partial<Record<AgentStatus, { stroke: string; dash?: string; width: number }>> = {
  running: { stroke: '#2F5BD3', width: 2.5 },
  waiting: { stroke: '#A15C00', width: 2, dash: '4 4' },
  queued: { stroke: '#A15C00', width: 2, dash: '4 4' },
};

/** Global skills in the middle, project skills inside their project, live threads linked to the skills they use. */
export function SkillsMapView(o: {
  nodes: SkillNode[];
  projects: Array<{ id: string; name: string; tone: MapTone }>;
  catalogKeys?: Set<string>;
  selected: string | null;
  onSelect(key: string): void;
}) {
  return (
    <MapCanvas label="Skill map">
      {({ width, height }) => {
        const l = layoutSkillsMap({ nodes: o.nodes, projects: o.projects, width, height });
        const byKey = new Map(o.nodes.map((n) => [n.key, n]));
        return (
          <>
            <svg width={width} height={height} aria-hidden="true" className="skills-svg">
              {l.globalRadius ? <circle cx={l.center.x} cy={l.center.y} r={l.globalRadius} fill="none" stroke="#D3CCBE" strokeDasharray="3 6" /> : null}
              {l.territories.map((t) => (
                <circle key={t.projectId} cx={t.x} cy={t.y} r={t.r} fill={TERRITORY[t.tone].fill} stroke={TERRITORY[t.tone].stroke} />
              ))}
              {l.shadows.map((s) => (
                <path key={s.toKey} d={s.d} fill="none" stroke="#A15C00" strokeWidth="1.5" strokeDasharray="5 5" />
              ))}
              {l.markers.flatMap((m) =>
                m.to.map((p, i) => {
                  const line = LINE[m.status] ?? { stroke: '#8A857B', width: 2 };
                  return <path key={`${m.threadId}-${i}`} d={`M${m.x} ${m.y} L ${p.x} ${p.y}`} stroke={line.stroke} strokeWidth={line.width} strokeDasharray={line.dash} />;
                }),
              )}
            </svg>
            {l.territories.map((t) => (
              <span key={t.projectId} className="skills-territory-label" style={{ left: t.x, top: t.y - t.r - 22, color: TERRITORY[t.tone].text }}>
                {t.name}
                {t.count ? null : <span className="muted small"> · no project skills</span>}
              </span>
            ))}
            {l.globalRadius || o.nodes.some((n) => n.scope === 'global') ? (
              <span className="skills-global-label" style={{ left: l.center.x, top: l.center.y + l.globalRadius + 48 }}>
                GLOBAL
              </span>
            ) : null}
            {l.shadows.slice(0, 1).map((s) => (
              <span key={s.toKey} className="skills-shadow-label" style={{ left: s.mx, top: s.my - 40 }}>
                shadowed by
              </span>
            ))}
            {l.skills.map((s) => {
              const n = byKey.get(s.key)!;
              const shadowed = n.scope === 'global' && n.shadowedIn.length > 0;
              const cat = o.catalogKeys?.has(s.key) ?? false;
              return (
                <button
                  key={s.key}
                  type="button"
                  className={`skill-node ${n.scope}${shadowed ? ' shadowed' : ''}${n.error ? ' broken' : ''}${cat ? ' from-catalog' : ''}${o.selected === s.key ? ' selected' : ''}`}
                  style={{ left: s.x, top: s.y, width: s.r * 2, height: s.r * 2 }}
                  aria-label={`${n.name}, ${n.scope === 'global' ? 'global' : `${n.projectName ?? 'project'} project skill`}, version ${n.version}${shadowed ? ', shadowed' : ''}${cat ? ', from the catalog' : ''}${n.usedBy.length ? `, used by ${n.usedBy.length}` : ''}`}
                  aria-pressed={o.selected === s.key}
                  onClick={() => o.onSelect(s.key)}
                >
                  <span className="skill-node-name">{n.name}</span>
                  <span className="skill-node-v">v{n.version}</span>
                </button>
              );
            })}
            {l.markers.map((m) => (
              <a key={m.threadId} className={`skill-marker status-${m.status}`} style={{ left: m.x, top: m.y }} href={href({ name: 'project', id: m.projectId, tab: 'threads', threadId: m.threadId })}>
                <span className="skill-marker-dot" aria-hidden="true" />
                {m.title}
              </a>
            ))}
          </>
        );
      }}
    </MapCanvas>
  );
}
