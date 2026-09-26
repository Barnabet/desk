import type { AgentStatus, ProjectSummary } from '@desk/protocol';

/** An axis-aligned box on the map: its top-left corner and size, in pixels. */
export type Box = { x: number; y: number; w: number; h: number };
/** Where a renderer anchors an element (see map.css for which point of it), and the box it is estimated to cover. */
export type Placed = { x: number; y: number; box: Box };

export type MapProject = {
  id: string;
  activity: number;
  threads: Array<{ id: string; status: AgentStatus; title?: string | null }>;
  /** The territory label's two lines, the project's name and its summary; they size the label the sun's must stay clear of. */
  label?: readonly [string, string];
  /** Whether an attention callout is pinned above its Desk; its moons then keep out from under the card. */
  callout?: boolean;
};
export type Territory = {
  id: string;
  x: number;
  y: number;
  r: number;
  desk: number;
  orbit: number;
  /** The name and summary: anchored at their bottom centre, straddling the top of the disc. */
  label: Placed;
  /** Where the attention callout goes when the project has one: anchored at its pin's centre on the card's bottom edge. */
  callout: Placed;
  threads: Array<{ id: string; x: number; y: number; box: Box }>;
};
/** `sunLabel` is the two lines under deskd's sun, anchored at their top centre. */
export type MapLayout = { sun: { x: number; y: number }; sunLabel: Placed; rings: number[]; territories: Territory[] };

const TAU = Math.PI * 2;

/*
 * Sizes from map.css (Geist, body line-height 1.45), so the layout can keep text apart without measuring it. A character
 * is taken at 0.6em, a little wider than Geist's average, so the estimated boxes err large.
 */
const EM = 0.6;
const LINE = 1.45;
const text = (s: string, px: number) => s.length * px * EM;
/** `.orbit-sun-label`: two 11.5px lines, 40px below the sun's centre. */
const SUN_LABEL_TOP = 40;
const SUN_LABEL_H = 2 * 11.5 * LINE;
/** `.orbit-label`: a 13px name (and a 7px unread dot 6px after it), 2px, an 11.5px summary; its bottom 14px inside the disc. */
const LABEL_INSET = 14;
const LABEL_H = 13 * LINE + 2 + 11.5 * LINE;
const UNREAD = 13;
/** `.orbit-callout`: a 22px pin centred on the anchor, 8px, a 200px card of 12px text at line-height 1.35 (two title lines). */
const CALLOUT_LIFT = 18;
const PIN = 22;
const CALLOUT_W = PIN + 8 + 200;
const CALLOUT_H = 12 + 11 * 1.35 + 1 + 2 * 12 * 1.35;
/** `.orbit-thread`: a 14px dot with a 4px halo centred on the moon, 6px, then 12px text running away from the disc. */
const DOT_HALF = 7;
const MOON_HALF = DOT_HALF + 4;
const MOON_TEXT_GAP = 6;
/** The clearance kept between a label and the sun's, and between a callout and a moon. */
const GAP = 4;

const lineWidth = (lines: readonly string[], px: number) => Math.max(0, ...lines.map((l) => text(l, px)));

/** The territory label's box, anchored at its bottom centre `labelY`. */
const labelBox = (x: number, labelY: number, w: number): Box => ({ x: x - w / 2, y: labelY - LABEL_H, w, h: LABEL_H });

const intersects = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/**
 * The arc a territory's moons go on, clockwise from its start: the whole orbit from the upper right, or, under a
 * callout, the part the card leaves free. A moon's label runs right of its dot on the right half and left of it on the
 * left half, so a moon clears the card (pin on the Desk's top, card to its right) when it sits below the card's bottom
 * edge, or on the left half far enough left of the pin.
 */
function moonArc(orbit: number, desk: number, callout: boolean): [number, number] {
  if (!callout) return [-Math.PI / 3, TAU];
  const below = (desk / 2 + CALLOUT_LIFT - GAP - MOON_HALF) / orbit;
  if (below >= 1) return [-Math.PI / 3, TAU];
  const leftOf = (PIN / 2 + GAP + MOON_HALF) / orbit;
  const end = -Math.asin(below);
  const start = leftOf >= 1 ? -Math.PI + Math.asin(below) : Math.max(-Math.PI + Math.asin(below), -Math.acos(-leftOf));
  return [end, TAU - (end - start)];
}

function moonBox(x: number, y: number, left: boolean, label: string): Box {
  const w = MOON_HALF + DOT_HALF + MOON_TEXT_GAP + text(label, 12);
  return { x: left ? x + MOON_HALF - w : x - MOON_HALF, y: y - MOON_HALF, w, h: 2 * MOON_HALF };
}

/** How much is going on in a project: sizes its territory and orders it toward the centre. */
export function activityOf(p: ProjectSummary): number {
  const count = (...s: AgentStatus[]) => p.threads.filter((t) => s.includes(t.status)).length;
  return 1 + count('running') * 3 + count('waiting', 'queued') * 2 + p.threads.length + p.attention_count * 2 + (p.desk_status === 'running' ? 2 : 0);
}

/**
 * Deterministic orbit layout: deskd in the middle, the busiest projects on the inner ring and largest,
 * then a relaxation pass so territories never overlap, stay in bounds, keep clear of the sun and keep their labels off
 * the sun's. A territory with a callout puts its moons on the arc the card leaves free.
 * `sunLabel` is the text under the sun, which sizes the box territory labels keep out of.
 */
export function layoutMap(projects: MapProject[], width: number, height: number, sunLabel: readonly string[] = []): MapLayout {
  const sun = { x: width / 2, y: height / 2 + 20 };
  // Without the text (or a project's label), a typical width stands in.
  const sunLabelW = sunLabel.length ? lineWidth(sunLabel, 11.5) : 200;
  const sunText: Placed = { x: sun.x, y: sun.y + SUN_LABEL_TOP, box: { x: sun.x - sunLabelW / 2, y: sun.y + SUN_LABEL_TOP, w: sunLabelW, h: SUN_LABEL_H } };
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
    const labelW = p.label ? Math.max(text(p.label[0], 13) + UNREAD, text(p.label[1], 11.5)) : 160;
    return { p, r, labelW, x: sun.x + Math.cos(angle) * rings[ring]!, y: sun.y + Math.sin(angle) * rings[ring]! * 0.8 };
  });
  const fits = (v: number, r: number, size: number) => v >= r + 8 && v <= size - r - 8;
  const { box: s } = sunText;
  const avoid: Box = { x: s.x - GAP, y: s.y - GAP, w: s.w + 2 * GAP, h: s.h + 2 * GAP };
  /**
   * Moves a territory whose label would cross the sun's out of its way, vertically or sideways (away from the sun
   * either way): sideways when that stays in bounds and is shorter, or when the vertical move would leave the canvas.
   */
  const keepLabelOffSun = (a: (typeof nodes)[number]) => {
    const b = labelBox(a.x, a.y - a.r + LABEL_INSET, a.labelW);
    if (!intersects(b, avoid)) return;
    const dy = a.y >= sun.y ? avoid.y + avoid.h - b.y : avoid.y - (b.y + b.h);
    const dx = a.x >= sun.x ? avoid.x + avoid.w - b.x : avoid.x - (b.x + b.w);
    if (fits(a.x + dx, a.r, width) && (Math.abs(dx) < Math.abs(dy) || !fits(a.y + dy, a.r, height))) a.x += dx;
    else a.y += dy;
  };
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
      keepLabelOffSun(a);
      a.x = Math.min(width - a.r - 8, Math.max(a.r + 8, a.x));
      a.y = Math.min(height - a.r - 8, Math.max(a.r + 8, a.y));
    }
  }
  const territories = nodes.map(({ p, x, y, r, labelW }): Territory => {
    const orbit = r * 0.62;
    const desk = Math.round(40 + (r / maxR) * 24);
    const n = p.threads.length;
    const [start, arc] = moonArc(orbit, desk, !!p.callout);
    const labelY = y - r + LABEL_INSET;
    const calloutY = y - desk / 2 - CALLOUT_LIFT;
    return {
      id: p.id,
      x,
      y,
      r,
      orbit,
      desk,
      label: { x, y: labelY, box: labelBox(x, labelY, labelW) },
      callout: { x, y: calloutY, box: { x: x - PIN / 2, y: calloutY - CALLOUT_H, w: CALLOUT_W, h: CALLOUT_H } },
      threads: p.threads.map((t, i) => {
        const a = start + (i / Math.max(1, n)) * arc;
        const tx = x + Math.cos(a) * orbit;
        const ty = y + Math.sin(a) * orbit;
        return { id: t.id, x: tx, y: ty, box: moonBox(tx, ty, tx < x, `${t.title ?? 'Thread'}${t.status === 'done' ? ' · done' : ''}`) };
      }),
    };
  });
  return { sun, sunLabel: sunText, rings, territories };
}
