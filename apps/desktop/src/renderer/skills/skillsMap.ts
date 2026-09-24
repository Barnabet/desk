import type { SkillNode } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';

export type MapTone = 'running' | 'waiting' | 'idle';
export type PlacedSkill = { key: string; x: number; y: number; r: number };
export type Territory = { projectId: string; name: string; tone: MapTone; x: number; y: number; r: number; count: number };
export type UsageMarker = { threadId: string; projectId: string; title: string; status: AgentStatus; x: number; y: number; to: Array<{ x: number; y: number }> };
export type ShadowLink = { fromKey: string; toKey: string; d: string; mx: number; my: number };
export type SkillsMapLayout = {
  width: number;
  height: number;
  center: { x: number; y: number };
  globalRadius: number;
  skills: PlacedSkill[];
  territories: Territory[];
  markers: UsageMarker[];
  shadows: ShadowLink[];
};

const LIVE = new Set<AgentStatus>(['running', 'waiting', 'queued']);
const skillR = (n: SkillNode, base: number) => base + Math.min(24, 6 * n.usedBy.length);
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/**
 * The skills map: global skills on a ring in the middle; each project a territory on an outer
 * ellipse holding its own skills; markers for live threads linked to the skills they use; and a
 * dashed link from a global skill to each project skill that shadows it.
 */
export function layoutSkillsMap(o: { nodes: SkillNode[]; projects: Array<{ id: string; name: string; tone: MapTone }>; width: number; height: number }): SkillsMapLayout {
  const { width: w, height: h } = o;
  const center = { x: w * 0.44, y: h * 0.52 };
  const globals = o.nodes.filter((n) => n.scope === 'global');
  const globalRadius = globals.length <= 1 ? 0 : Math.min(Math.min(w, h) * 0.2, 60 + 16 * globals.length);
  const skills = new Map<string, PlacedSkill>();
  globals.forEach((n, i) => {
    const a = (i / Math.max(1, globals.length)) * Math.PI * 2 - Math.PI / 2;
    skills.set(n.key, { key: n.key, x: center.x + Math.cos(a) * globalRadius, y: center.y + Math.sin(a) * globalRadius, r: skillR(n, 38) });
  });

  const territories: Territory[] = o.projects.map((p, i) => {
    const own = o.nodes.filter((n) => n.scope === 'project' && n.projectId === p.id);
    const r = 56 + 24 * Math.sqrt(own.length);
    const a = (i / Math.max(1, o.projects.length)) * Math.PI * 2 - Math.PI / 3;
    const x = clamp(center.x + Math.cos(a) * w * 0.36, r + 12, w - r - 12);
    const y = clamp(center.y + Math.sin(a) * h * 0.36, r + 40, h - r - 12);
    own.forEach((n, k) => {
      const ring = own.length === 1 ? 0 : r * 0.45;
      const b = (k / own.length) * Math.PI * 2 - Math.PI / 2;
      skills.set(n.key, { key: n.key, x: x + Math.cos(b) * ring, y: y + Math.sin(b) * ring, r: skillR(n, 32) });
    });
    return { projectId: p.id, name: p.name, tone: p.tone, x, y, r, count: own.length };
  });

  const byThread = new Map<string, { title: string; status: AgentStatus; projectId: string; keys: string[] }>();
  for (const n of o.nodes)
    for (const u of n.usedBy) {
      if (!LIVE.has(u.status)) continue;
      const t = byThread.get(u.threadId) ?? { title: u.title ?? 'Thread', status: u.status, projectId: u.projectId, keys: [] };
      t.keys.push(n.key);
      byThread.set(u.threadId, t);
    }
  const markers: UsageMarker[] = [];
  for (const [threadId, t] of byThread) {
    const to = t.keys.map((k) => skills.get(k)).filter((s): s is PlacedSkill => !!s);
    if (!to.length) continue;
    const mx = to.reduce((a, s) => a + s.x, 0) / to.length;
    const my = to.reduce((a, s) => a + s.y, 0) / to.length;
    const home = territories.find((x) => x.projectId === t.projectId);
    // Sit between the skills and the thread's project, a little off the skills themselves.
    const tx = home ? mx + (home.x - mx) * 0.35 : mx + 60;
    const ty = home ? my + (home.y - my) * 0.35 : my + 40;
    const k = markers.filter((m) => Math.hypot(m.x - tx, m.y - ty) < 30).length;
    markers.push({ threadId, projectId: t.projectId, title: t.title, status: t.status, x: tx, y: ty + k * 24, to: to.map((s) => ({ x: s.x, y: s.y })) });
  }

  const shadows: ShadowLink[] = [];
  for (const n of o.nodes) {
    if (n.scope !== 'project' || !n.shadows) continue;
    const from = skills.get(`global:${n.name}`);
    const to = skills.get(n.key);
    if (!from || !to) continue;
    const mx = (from.x + to.x) / 2;
    const my = (from.y + to.y) / 2 - 60;
    shadows.push({ fromKey: from.key, toKey: to.key, d: `M${from.x} ${from.y} Q ${mx} ${my} ${to.x} ${to.y}`, mx, my: my + 30 });
  }
  return { width: w, height: h, center, globalRadius, skills: [...skills.values()], territories, markers, shadows };
}
