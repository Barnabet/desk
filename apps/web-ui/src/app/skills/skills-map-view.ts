import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, input, output, signal } from '@angular/core';
import type { SkillNode } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';
import { href, layoutSkillsMap, type MapTone, type SkillTerritory, type UsageMarker } from '@desk/ui-core';
import { MapCanvas, DEFAULT_CANVAS_SIZE, type CanvasSize } from '../map/map-canvas';

const TERRITORY: Record<MapTone, { fill: string; stroke: string; text: string }> = {
  running: { fill: 'var(--run-pastel)', stroke: 'var(--run-ring)', text: 'var(--run-text)' },
  waiting: { fill: 'var(--wait-pastel)', stroke: 'var(--wait-ring)', text: 'var(--wait-text)' },
  idle: { fill: 'var(--idle-pastel)', stroke: 'var(--rule)', text: 'var(--text-min)' },
};
/** A live thread's line by its status (a Map, so a status from outside the UI never reads a prototype member). */
const LINE: ReadonlyMap<AgentStatus, { stroke: string; dash?: string; width: number }> = new Map<AgentStatus, { stroke: string; dash?: string; width: number }>([
  ['running', { stroke: 'var(--run)', width: 2.5 }],
  ['waiting', { stroke: 'var(--wait)', width: 2, dash: '4 4' }],
  ['queued', { stroke: 'var(--wait)', width: 2, dash: '4 4' }],
]);

/**
 * Global skills in the middle, project skills inside their project, live threads linked to the skills they use
 * (SkillsMapView.tsx). Its React root is MapCanvas, so this host is `display: contents` around the canvas.
 */
@Component({
  selector: 'div[deskSkillsMapView]',
  imports: [MapCanvas],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskMapCanvas label="Skill map" (resized)="size.set($event)">
      <svg class="skills-svg" [attr.width]="size().width" [attr.height]="size().height" aria-hidden="true">
        @if (layout().globalRadius) {
          <circle [attr.cx]="layout().center.x" [attr.cy]="layout().center.y" [attr.r]="layout().globalRadius" fill="none" stroke="var(--orbit)" stroke-dasharray="3 6"></circle>
        }
        @for (t of layout().territories; track t.projectId) {
          <circle [attr.cx]="t.x" [attr.cy]="t.y" [attr.r]="t.r" [attr.fill]="tone(t).fill" [attr.stroke]="tone(t).stroke"></circle>
        }
        @for (s of layout().shadows; track s.toKey) {
          <path [attr.d]="s.d" fill="none" stroke="var(--wait)" stroke-width="1.5" stroke-dasharray="5 5"></path>
        }
        @for (l of lines(); track l.id) {
          <path [attr.d]="l.d" [attr.stroke]="l.stroke" [attr.stroke-width]="l.width" [attr.stroke-dasharray]="l.dash"></path>
        }
      </svg>
      @for (t of layout().territories; track t.projectId) {
        <span class="skills-territory-label" [style.left.px]="t.x" [style.top.px]="t.y - t.r - 22" [style.color]="tone(t).text">{{ t.name }}@if (!t.count) {<span class="muted small"> · no project skills</span>}</span>
      }
      @if (showGlobal()) {
        <span class="skills-global-label" [style.left.px]="layout().center.x" [style.top.px]="layout().center.y + layout().globalRadius + 48">GLOBAL</span>
      }
      @for (s of layout().shadows.slice(0, 1); track s.toKey) {
        <span class="skills-shadow-label" [style.left.px]="s.mx" [style.top.px]="s.my - 40">shadowed by</span>
      }
      @for (s of skills(); track s.key) {
        <button type="button" class="skill-node" [class]="s.scope" [class.shadowed]="s.shadowed" [class.broken]="s.broken" [class.from-catalog]="s.fromCatalog" [class.selected]="s.pressed" [style.left.px]="s.x" [style.top.px]="s.y" [style.width.px]="s.d" [style.height.px]="s.d" [attr.aria-label]="s.label" [attr.aria-pressed]="s.pressed" (click)="selectSkill.emit(s.key)">
          <span class="skill-node-name">{{ s.name }}</span>
          <span class="skill-node-v">v{{ s.version }}</span>
        </button>
      }
      @for (m of layout().markers; track m.threadId) {
        <a class="skill-marker" [class]="'status-' + m.status" [style.left.px]="m.x" [style.top.px]="m.y" [href]="markerHref(m)"><span class="skill-marker-dot" aria-hidden="true"></span>{{ m.title }}</a>
      }
    </div>
  `,
})
export class SkillsMapView {
  readonly nodes = input.required<SkillNode[]>();
  readonly projects = input.required<Array<{ id: string; name: string; tone: MapTone }>>();
  /** Keys of skills installed from the catalog. */
  readonly catalogKeys = input<ReadonlySet<string>>();
  readonly selected = input<string | null>(null);
  /** The skill picked (React's `onSelect`). */
  readonly selectSkill = output<string>();

  /** The canvas's measured size (React's render-prop argument). */
  protected readonly size = signal<CanvasSize>(DEFAULT_CANVAS_SIZE);
  protected readonly layout = computed(() => layoutSkillsMap({ nodes: this.nodes(), projects: this.projects(), width: this.size().width, height: this.size().height }));
  protected readonly lines = computed(() =>
    this.layout().markers.flatMap((m) =>
      m.to.map((p, i) => {
        const line = LINE.get(m.status) ?? { stroke: 'var(--muted)', width: 2 };
        return { id: `${m.threadId}-${i}`, d: `M${m.x} ${m.y} L ${p.x} ${p.y}`, stroke: line.stroke, width: line.width, dash: line.dash ?? null };
      }),
    ),
  );
  protected readonly showGlobal = computed(() => this.layout().globalRadius > 0 || this.nodes().some((n) => n.scope === 'global'));
  protected readonly skills = computed(() => {
    const byKey = new Map(this.nodes().map((n) => [n.key, n] as const));
    const catalog = this.catalogKeys();
    const selected = this.selected();
    return this.layout().skills.flatMap((s) => {
      const n = byKey.get(s.key);
      if (!n) return [];
      const shadowed = n.scope === 'global' && n.shadowedIn.length > 0;
      const fromCatalog = catalog?.has(s.key) ?? false;
      const where = n.scope === 'global' ? 'global' : `${n.projectName ?? 'project'} project skill`;
      return [
        {
          key: s.key,
          x: s.x,
          y: s.y,
          d: s.r * 2,
          name: n.name,
          version: n.version,
          pressed: selected === s.key,
          scope: n.scope,
          shadowed,
          broken: !!n.error,
          fromCatalog,
          label: `${n.name}, ${where}, version ${n.version}${shadowed ? ', shadowed' : ''}${fromCatalog ? ', from the catalog' : ''}${n.usedBy.length ? `, used by ${n.usedBy.length}` : ''}`,
        },
      ];
    });
  });

  protected tone(t: SkillTerritory): { fill: string; stroke: string; text: string } {
    return TERRITORY[t.tone];
  }

  protected markerHref(m: UsageMarker): string {
    return href({ name: 'project', id: m.projectId, tab: 'threads', threadId: m.threadId });
  }
}
