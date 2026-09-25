import type { AgentStatus, ProjectSummary } from '@desk/protocol';

export type MapProject = { id: string; activity: number; threads: Array<{ id: string; status: AgentStatus }> };
export type Territory = { id: string; x: number; y: number; r: number; desk: number; orbit: number; threads: Array<{ id: string; x: number; y: number }> };
export type MapLayout = { sun: { x: number; y: number }; rings: number[]; territories: Territory[] };

const TAU = Math.PI * 2;

/** How much is going on in a project: sizes its territory and orders it toward the centre. */
export function activityOf(p: ProjectSummary): number {
  const count = (...s: AgentStatus[]) => p.threads.filter((t) => s.includes(t.status)).length;
  return 1 + count('running') * 3 + count('waiting', 'queued') * 2 + p.threads.length + p.attention_count * 2 + (p.desk_status === 'running' ? 2 : 0);
}

/**
 * Deterministic orbit layout: deskd in the middle, the busiest projects on the inner ring and largest,
 * then a relaxation pass so territories never overlap, stay in bounds and keep clear of the sun.
 */
export function layoutMap(projects: MapProject[], width: number, height: number): MapLayout {
  const sun = { x: width / 2, y: height / 2 + 20 };
  const span = Math.min(width, height);
  const rings = [0.26, 0.42, 0.58].map((f) => f * span);
  const maxR = span * 0.19;
  const minR = span * 0.08;
  const sorted = [...projects].sort((a, b) => b.activity - a.activity || a.id.localeCompare(b.id));
  const slots = [3, 6, Math.max(0, sorted.length - 9)];
  const nodes = sorted.map((p, i) => {
    const ring = i < 3 ? 0 : i < 9 ? 1 : 2;
    const k = ring === 0 ? i : ring === 1 ? i - 3 : i - 9;
    const inRing = Math.max(1, Math.min(slots[ring]!, sorted.length - (ring === 0 ? 0 : ring === 1 ? 3 : 9)));
    const angle = -Math.PI / 2 + ring * 0.6 + (k / inRing) * TAU;
    const r = Math.min(maxR, minR + Math.sqrt(p.activity) * span * 0.022);
    return { p, r, x: sun.x + Math.cos(angle) * rings[ring]!, y: sun.y + Math.sin(angle) * rings[ring]! * 0.8 };
  });
  for (let iter = 0; iter < 120; iter++) {
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i]!;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j]!;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 0.01;
        const min = a.r + b.r + 24;
        if (d < min) {
          const push = (min - d) / 2;
          a.x -= (dx / d) * push;
          a.y -= (dy / d) * push;
          b.x += (dx / d) * push;
          b.y += (dy / d) * push;
        }
      }
      const ds = Math.hypot(a.x - sun.x, a.y - sun.y) || 0.01;
      const clear = a.r + 64;
      if (ds < clear) {
        a.x = sun.x + ((a.x - sun.x) / ds) * clear;
        a.y = sun.y + ((a.y - sun.y) / ds) * clear;
      }
      a.x = Math.min(width - a.r - 8, Math.max(a.r + 8, a.x));
      a.y = Math.min(height - a.r - 8, Math.max(a.r + 8, a.y));
    }
  }
  const territories = nodes.map(({ p, x, y, r }) => {
    const orbit = r * 0.62;
    const n = p.threads.length;
    return {
      id: p.id,
      x,
      y,
      r,
      orbit,
      desk: Math.round(40 + (r / maxR) * 24),
      threads: p.threads.map((t, i) => {
        const a = -Math.PI / 3 + (i / Math.max(1, n)) * TAU;
        return { id: t.id, x: x + Math.cos(a) * orbit, y: y + Math.sin(a) * orbit };
      }),
    };
  });
  return { sun, rings, territories };
}
