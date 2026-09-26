import type { AgentStatus, AttentionItem, ProjectSummary } from '@desk/protocol';

export type MiniLane = {
  id: string;
  projectId: string;
  title: string;
  status: AgentStatus;
  y: number;
  d: string;
  color: string;
  text: string;
  dashed: boolean;
  end: { x: number; kind: 'train' | 'signal' | 'stalled' | 'stop' | 'none' };
};
export type MiniLine = { width: number; height: number; trunkY: number; x0: number; nowX: number; trunk: string; lanes: MiniLane[] };

const COLOR: Record<AgentStatus, [string, string]> = {
  running: ['var(--run)', 'var(--run-text)'],
  waiting: ['var(--wait)', 'var(--wait-text)'],
  queued: ['var(--wait)', 'var(--wait-text)'],
  idle: ['var(--muted-soft)', 'var(--text-min)'],
  done: ['var(--muted)', 'var(--text-min)'],
  cancelled: ['var(--muted)', 'var(--text-min)'],
  failed: ['var(--accent)', 'var(--accent)'],
};
const RANK: Record<AgentStatus, number> = { waiting: 1, running: 2, queued: 3, failed: 4, idle: 5, done: 6, cancelled: 7 };

/**
 * Today at a glance for the menu-bar popover: Desk's trunk across every project, and the busiest
 * threads forking off it, newest nearest the trunk. Threads that need you end in a signal.
 */
export function miniLine(o: { overview: ProjectSummary[]; attention: AttentionItem[]; now: number; width?: number; height?: number; maxLanes?: number }): MiniLine {
  const width = o.width ?? 378;
  const height = o.height ?? 118;
  const maxLanes = o.maxLanes ?? 4;
  const x0 = 8;
  const nowX = width - 164;
  const trunkY = 18;
  const signal = new Set(o.attention.filter((i) => i.kind === 'approval' || i.kind === 'failed').map((i) => i.ref.thread_id ?? i.agent_id));
  const stalled = new Set(o.attention.filter((i) => i.kind === 'stalled').map((i) => i.ref.thread_id ?? i.agent_id));
  const dayAgo = o.now - 24 * 3_600_000;
  const threads = o.overview
    .flatMap((p) => p.threads.map((t) => ({ ...t, projectId: p.project.id })))
    .filter((t) => t.status === 'running' || t.status === 'waiting' || t.status === 'queued' || Date.parse(t.updated_at) >= dayAgo)
    .sort((a, b) => Number(signal.has(b.id) || stalled.has(b.id)) - Number(signal.has(a.id) || stalled.has(a.id)) || RANK[a.status] - RANK[b.status] || b.updated_at.localeCompare(a.updated_at))
    .slice(0, maxLanes)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const start = Math.min(o.now - 30 * 60_000, ...threads.map((t) => Date.parse(t.created_at)));
  const x = (ts: string) => x0 + 8 + ((Date.parse(ts) - start) / Math.max(1, o.now - start)) * (nowX - x0 - 24);
  const gap = Math.min(20, (height - trunkY - 20) / Math.max(1, threads.length));
  const lanes = threads.map((t, i): MiniLane => {
    const y = trunkY + gap * (i + 1);
    const xf = Math.min(x(t.created_at), nowX - 24);
    const live = t.status === 'running';
    const endX = live ? nowX : Math.max(xf + 16, Math.min(nowX - 6, x(t.updated_at)));
    const [color, text] = COLOR[t.status];
    const r = Math.min(8, (y - trunkY) / 2);
    return {
      id: t.id,
      projectId: t.projectId,
      title: `${t.title ?? 'Thread'}${stalled.has(t.id) ? ' · stalled' : ''}`,
      status: t.status,
      y,
      d: `M${xf} ${trunkY} Q ${xf + r} ${trunkY} ${xf + r} ${trunkY + r} V ${y - r} Q ${xf + r} ${y} ${xf + 2 * r} ${y} H ${endX}`,
      color,
      text,
      dashed: t.status === 'queued' || stalled.has(t.id),
      end: { x: endX, kind: live ? 'train' : signal.has(t.id) ? 'signal' : stalled.has(t.id) ? 'stalled' : t.status === 'done' ? 'stop' : 'none' },
    };
  });
  return { width, height, trunkY, x0, nowX, trunk: `M${x0} ${trunkY} H ${nowX}`, lanes };
}
