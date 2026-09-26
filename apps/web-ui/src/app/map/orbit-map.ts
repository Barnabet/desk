import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, output } from '@angular/core';
import type { AgentStatus, AttentionItem, ProjectSummary } from '@desk/protocol';
import { ago, href, type MapLayout } from '@desk/ui-core';
import { RouteService } from '../core/route.service';
import { Unread } from '../core/unread';
import { projectSummaryLine, projectTone, type Tone } from './project-summary';

type ToneColors = { fill: string; stroke: string; orbit: string; text: string };

const TONE: Record<Tone, ToneColors> = {
  running: { fill: '#E3E8F5', stroke: '#C9D3EC', orbit: '#B7C4E6', text: '#1F45A8' },
  waiting: { fill: '#F3E6CF', stroke: '#E6D3AF', orbit: '#E0C99E', text: '#7A4500' },
  idle: { fill: '#E6E1D7', stroke: '#D6CFC1', orbit: '#D6CFC1', text: '#4A4740' },
};

const SPOKE: Partial<Record<AgentStatus, { stroke: string; width: number; dash?: string }>> = {
  running: { stroke: '#2F5BD3', width: 2.5 },
  waiting: { stroke: '#A15C00', width: 2, dash: '4 4' },
  queued: { stroke: '#A15C00', width: 2, dash: '4 4' },
  failed: { stroke: '#C4441C', width: 2, dash: '4 4' },
};
const QUIET_SPOKE = { stroke: '#B7C4E6', width: 1.5, dash: '2 4' };

const CALLOUT: Record<AttentionItem['kind'], { label: string; glyph: string }> = {
  approval: { label: 'Approval', glyph: '!' },
  question: { label: 'Question', glyph: '?' },
  needs_you: { label: 'From a report', glyph: '' },
  stalled: { label: 'Stalled', glyph: '' },
  failed: { label: 'Failed', glyph: '!' },
  paused: { label: 'Agents paused', glyph: '' },
};

type Spoke = { id: string; d: string; stroke: string; width: number; dash: string | null };
type Moon = { id: string; cls: string; x: number; y: number; href: string; label: string };
type Callout = { top: number; href: string; title: string; pin: string; kind: string };
type TerritoryView = {
  id: string;
  p: ProjectSummary;
  x: number;
  y: number;
  r: number;
  orbit: number;
  desk: number;
  tone: Tone;
  colors: ToneColors;
  labelTop: number;
  summary: string;
  selected: boolean;
  shadow: string;
  spokes: Spoke[];
  moons: Moon[];
  callout: Callout | null;
};

/**
 * The orbit map (design C territories), ported from OrbitMap.tsx: deskd's sun in the middle, one territory per project
 * with its Desk disc, its threads on the orbit, and a callout for the first attention item. The host takes no box
 * (`display: contents`), so its children sit in `.map-layer` as the React fragment's did.
 */
@Component({
  selector: 'div[deskOrbitMap]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { style: 'display: contents' },
  template: `
    <svg class="orbit-svg" [attr.width]="width()" [attr.height]="height()" aria-hidden="true">
      @for (r of layout().rings; track $index) {
        <circle [attr.cx]="layout().sun.x" [attr.cy]="layout().sun.y" [attr.r]="r" fill="none" stroke="#D3CCBE" stroke-dasharray="3 6"></circle>
      }
      @for (v of views(); track v.id) {
        <g>
          <circle [attr.cx]="v.x" [attr.cy]="v.y" [attr.r]="v.r" [attr.fill]="v.colors.fill" [attr.stroke]="v.colors.stroke"></circle>
          <circle [attr.cx]="v.x" [attr.cy]="v.y" [attr.r]="v.orbit" fill="none" [attr.stroke]="v.colors.orbit" stroke-dasharray="3 6"></circle>
          @for (s of v.spokes; track s.id) {
            <path [attr.d]="s.d" [attr.stroke]="s.stroke" [attr.stroke-width]="s.width" [attr.stroke-dasharray]="s.dash"></path>
          }
        </g>
      }
    </svg>

    <button type="button" class="orbit-sun" [style.left.px]="layout().sun.x" [style.top.px]="layout().sun.y" [attr.aria-label]="sunAria()" (click)="openSystem()">deskd</button>
    <div class="orbit-sun-label" [style.left.px]="layout().sun.x" [style.top.px]="layout().sun.y + 40">
      <span>{{ sunLabel()[0] }}</span>
      <span>{{ sunLabel()[1] }}</span>
    </div>

    @for (v of views(); track v.id) {
      <div>
        <div class="orbit-label" [style.left.px]="v.x" [style.top.px]="v.labelTop" [style.color]="v.colors.text">
          <span class="orbit-label-name">{{ v.p.project.name }}@if (unread.isUnread(v.p)) {<span class="unread-dot" aria-label="unread"></span>}</span>
          <span class="orbit-label-sub">{{ v.summary }}</span>
        </div>
        <button
          type="button"
          [class]="'orbit-desk orbit-desk-' + v.tone"
          [style.left.px]="v.x"
          [style.top.px]="v.y"
          [style.width.px]="v.desk"
          [style.height.px]="v.desk"
          [style.box-shadow]="v.shadow"
          [attr.aria-label]="v.p.project.name + ': show details'"
          [attr.aria-pressed]="v.selected"
          (click)="pick.emit(v.id)"
        >Desk</button>
        @for (m of v.moons; track m.id) {
          <a [class]="m.cls" [style.left.px]="m.x" [style.top.px]="m.y" [href]="m.href"><span class="orbit-thread-dot" aria-hidden="true"></span>{{ m.label }}</a>
        }
        @if (v.callout; as c) {
          <a class="orbit-callout" [style.left.px]="v.x" [style.top.px]="c.top" [href]="c.href" [attr.aria-label]="c.title">
            <span class="orbit-pin" aria-hidden="true">{{ c.pin }}</span>
            <span class="orbit-callout-card">
              <span class="orbit-callout-kind">{{ c.kind }}</span>
              <span>{{ c.title }}</span>
            </span>
          </a>
        }
      </div>
    }
  `,
})
export class OrbitMap {
  readonly layout = input.required<MapLayout>();
  readonly projects = input.required<ProjectSummary[]>();
  readonly attention = input.required<AttentionItem[]>();
  /** The project whose territory the inspector shows. */
  readonly selected = input.required<string | null>();
  readonly width = input.required<number>();
  readonly height = input.required<number>();
  /** The two lines under the sun: version and connection, then the proxy and the running threads. */
  readonly sunLabel = input.required<[string, string]>();
  readonly sunAria = input.required<string>();
  readonly now = input.required<number>();
  /** A Desk disc was pressed: its project's id (React's onSelect). */
  readonly pick = output<string>();

  private readonly routes = inject(RouteService);
  protected readonly unread = inject(Unread);

  /** One view per territory, in layout order: everything the template draws, computed once per input change. */
  protected readonly views = computed((): TerritoryView[] => {
    const byId = new Map(this.projects().map((p) => [p.project.id, p]));
    const attention = this.attention();
    const selected = this.selected();
    const now = this.now();
    const views: TerritoryView[] = [];
    for (const t of this.layout().territories) {
      const p = byId.get(t.id);
      if (!p) continue;
      const tone = projectTone(p);
      const colors = TONE[tone];
      const items = attention.filter((i) => i.project_id === t.id);
      const first = items[0];
      views.push({
        id: t.id,
        p,
        x: t.x,
        y: t.y,
        r: t.r,
        orbit: t.orbit,
        desk: t.desk,
        tone,
        colors,
        labelTop: t.y - t.r + 14,
        summary: projectSummaryLine(p, items),
        selected: selected === t.id,
        shadow: selected === t.id ? `0 0 0 5px ${colors.fill}, 0 0 0 8px var(--accent)` : 'none',
        spokes: t.threads.map((pos) => {
          const th = p.threads.find((x) => x.id === pos.id);
          const s = (th && SPOKE[th.status]) ?? QUIET_SPOKE;
          return { id: pos.id, d: `M${t.x} ${t.y} L ${pos.x} ${pos.y}`, stroke: s.stroke, width: s.width, dash: s.dash ?? null };
        }),
        moons: t.threads.flatMap((pos) => {
          const th = p.threads.find((x) => x.id === pos.id);
          if (!th) return [];
          return [
            {
              id: th.id,
              cls: `orbit-thread orbit-thread-${th.status}${pos.x < t.x ? ' left' : ''}`,
              x: pos.x,
              y: pos.y,
              href: href({ name: 'project', id: t.id, tab: 'threads', threadId: th.id }),
              label: `${th.title ?? 'Thread'}${th.status === 'done' ? ' · done' : ''}`,
            },
          ];
        }),
        callout: first
          ? {
              top: t.y - t.desk / 2 - 18,
              href: href({ name: 'attention', item: first.id }),
              title: first.title,
              pin: items.length > 1 ? String(items.length) : CALLOUT[first.kind].glyph || '1',
              kind: `${CALLOUT[first.kind].label} · ${ago(first.created_at, now)}`,
            }
          : null,
      });
    }
    return views;
  });

  protected openSystem(): void {
    this.routes.navigate({ name: 'system' });
  }
}
