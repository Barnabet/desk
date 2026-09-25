import type { AgentStatus, AttentionItem, ProjectSummary } from '@desk/protocol';
import { ago } from '../format';
import { href } from '../router';
import { useUnread } from '../state/unread';
import type { MapLayout, Territory } from './layout';

export type Tone = 'running' | 'waiting' | 'idle';

/** Blue while anything runs, amber when it only waits on you, grey when idle (design C territories). */
export function projectTone(p: ProjectSummary): Tone {
  if (p.desk_status === 'running' || p.threads.some((t) => t.status === 'running' || t.status === 'queued')) return 'running';
  if (p.attention_count > 0 || p.desk_status === 'waiting' || p.threads.some((t) => t.status === 'waiting')) return 'waiting';
  return 'idle';
}

export function projectSummaryLine(p: ProjectSummary): string {
  const c = (...s: AgentStatus[]) => p.threads.filter((t) => s.includes(t.status)).length;
  const parts = [
    c('running') ? `${c('running')} running` : '',
    c('waiting', 'queued') ? `${c('waiting', 'queued')} waiting` : '',
    c('done') ? `${c('done')} done` : '',
    c('failed') ? `${c('failed')} failed` : '',
  ].filter(Boolean);
  if (parts.length) return parts.join(' · ');
  if (p.desk_status === 'waiting') return 'Desk is waiting on you';
  if (p.desk_status === 'running') return 'Desk is working';
  return p.plan_progress.total && p.plan_progress.done === p.plan_progress.total ? 'Idle · all plan items done' : 'Idle';
}

const TONE = {
  running: { fill: '#E3E8F5', stroke: '#C9D3EC', orbit: '#B7C4E6', text: '#1F45A8' },
  waiting: { fill: '#F3E6CF', stroke: '#E6D3AF', orbit: '#E0C99E', text: '#7A4500' },
  idle: { fill: '#E6E1D7', stroke: '#D6CFC1', orbit: '#D6CFC1', text: '#4A4740' },
} as const;

const SPOKE: Partial<Record<AgentStatus, { stroke: string; width: number; dash?: string }>> = {
  running: { stroke: '#2F5BD3', width: 2.5 },
  waiting: { stroke: '#A15C00', width: 2, dash: '4 4' },
  queued: { stroke: '#A15C00', width: 2, dash: '4 4' },
  failed: { stroke: '#C4441C', width: 2, dash: '4 4' },
};

const CALLOUT: Record<AttentionItem['kind'], { label: string; glyph: string }> = {
  approval: { label: 'Approval', glyph: '!' },
  question: { label: 'Question', glyph: '?' },
  needs_you: { label: 'From a report', glyph: '' },
  stalled: { label: 'Stalled', glyph: '' },
  failed: { label: 'Failed', glyph: '!' },
  paused: { label: 'Agents paused', glyph: '' },
};

function ProjectLabel({ p, t }: { p: ProjectSummary; t: Territory }) {
  const unread = useUnread(p);
  const tone = TONE[projectTone(p)];
  return (
    <div className="orbit-label" style={{ left: t.x, top: t.y - t.r + 14, color: tone.text }}>
      <span className="orbit-label-name">
        {p.project.name}
        {unread ? <span className="unread-dot" aria-label="unread" /> : null}
      </span>
      <span className="orbit-label-sub">{projectSummaryLine(p)}</span>
    </div>
  );
}

export function OrbitMap(o: {
  layout: MapLayout;
  projects: ProjectSummary[];
  attention: AttentionItem[];
  selected: string | null;
  onSelect(id: string): void;
  width: number;
  height: number;
  sunLabel: [string, string];
  sunAria: string;
  now: number;
}) {
  const byId = new Map(o.projects.map((p) => [p.project.id, p]));
  return (
    <>
      <svg className="orbit-svg" width={o.width} height={o.height} aria-hidden="true">
        {o.layout.rings.map((r) => (
          <circle key={r} cx={o.layout.sun.x} cy={o.layout.sun.y} r={r} fill="none" stroke="#D3CCBE" strokeDasharray="3 6" />
        ))}
        {o.layout.territories.map((t) => {
          const p = byId.get(t.id)!;
          const tone = TONE[projectTone(p)];
          return (
            <g key={t.id}>
              <circle cx={t.x} cy={t.y} r={t.r} fill={tone.fill} stroke={tone.stroke} />
              <circle cx={t.x} cy={t.y} r={t.orbit} fill="none" stroke={tone.orbit} strokeDasharray="3 6" />
              {t.threads.map((pos) => {
                const th = p.threads.find((x) => x.id === pos.id);
                const s = (th && SPOKE[th.status]) ?? { stroke: '#B7C4E6', width: 1.5, dash: '2 4' };
                return <path key={pos.id} d={`M${t.x} ${t.y} L ${pos.x} ${pos.y}`} stroke={s.stroke} strokeWidth={s.width} strokeDasharray={s.dash} />;
              })}
            </g>
          );
        })}
      </svg>

      <button type="button" className="orbit-sun" style={{ left: o.layout.sun.x, top: o.layout.sun.y }} aria-label={o.sunAria} onClick={() => (window.location.hash = '/system')}>
        deskd
      </button>
      <div className="orbit-sun-label" style={{ left: o.layout.sun.x, top: o.layout.sun.y + 40 }}>
        <span>{o.sunLabel[0]}</span>
        <span>{o.sunLabel[1]}</span>
      </div>

      {o.layout.territories.map((t) => {
        const p = byId.get(t.id)!;
        const tone = projectTone(p);
        const items = o.attention.filter((i) => i.project_id === t.id);
        const first = items[0];
        return (
          <div key={t.id}>
            <ProjectLabel p={p} t={t} />
            <button
              type="button"
              className={`orbit-desk orbit-desk-${tone}`}
              style={{ left: t.x, top: t.y, width: t.desk, height: t.desk, boxShadow: o.selected === t.id ? `0 0 0 5px ${TONE[tone].fill}, 0 0 0 8px var(--accent)` : 'none' }}
              aria-label={`${p.project.name}: show details`}
              aria-pressed={o.selected === t.id}
              onClick={() => o.onSelect(t.id)}
            >
              Desk
            </button>
            {t.threads.map((pos) => {
              const th = p.threads.find((x) => x.id === pos.id)!;
              const left = pos.x < t.x;
              return (
                <a
                  key={pos.id}
                  className={`orbit-thread orbit-thread-${th.status}${left ? ' left' : ''}`}
                  style={{ left: pos.x, top: pos.y }}
                  href={href({ name: 'project', id: t.id, tab: 'threads', threadId: th.id })}
                >
                  <span className="orbit-thread-dot" aria-hidden="true" />
                  {th.title ?? 'Thread'}
                  {th.status === 'done' ? ' · done' : ''}
                </a>
              );
            })}
            {first ? (
              <a className="orbit-callout" style={{ left: t.x, top: t.y - t.desk / 2 - 18 }} href={href({ name: 'attention', item: first.id })} aria-label={first.title}>
                <span className="orbit-pin" aria-hidden="true">
                  {items.length > 1 ? items.length : CALLOUT[first.kind].glyph || '1'}
                </span>
                <span className="orbit-callout-card">
                  <span className="orbit-callout-kind">
                    {CALLOUT[first.kind].label} · {ago(first.created_at, o.now)}
                  </span>
                  <span>{first.title}</span>
                </span>
              </a>
            ) : null}
          </div>
        );
      })}
    </>
  );
}
