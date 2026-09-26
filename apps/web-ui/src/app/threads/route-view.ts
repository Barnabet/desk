import { ChangeDetectionStrategy, Component, ElementRef, ViewEncapsulation, computed, inject, input, output } from '@angular/core';
import type { MessagesState } from '@desk/client';
import { plural, routeLayout, senderDisc, senderName, stopText, type Stop, type StopKind, type StopText } from '@desk/ui-core';
import { injectWidth } from '../core/width';

/** What each kind of stop shows on its disc. */
const INNER: Record<StopKind, (s: Stop, m: MessagesState) => string> = {
  brief: () => 'Brief',
  work: (s) =>
    s.tools.length ? `${s.tools.length} ${s.tools.length === 1 ? 'tool' : 'tools'}` : s.cards.length && !s.entries.some((e) => e.kind === 'assistant') ? '✉' : '¶',
  detour: () => 'detour',
  result: () => 'Report',
  revision: (s) => {
    const e = s.entries[0];
    return e?.kind === 'revision' ? `R${e.round}` : 'R';
  },
  steer: () => 'You',
  approval: () => '!',
  incoming: (s, m) => {
    const e = s.entries[0];
    return e?.kind === 'incoming' ? senderDisc(senderName(m, e.fromAgentId, e.fromLabel)) : 'Desk';
  },
  answer: () => '↩',
};

/** One disc, ready to draw. */
type PointView = { stop: Stop; x: number; y: number; r: number; t: StopText; label: string; inner: string; above: boolean };

/** A thread's route: numbered stops on a serpentine path, the live stretch in blue, and what comes next dashed. */
@Component({
  selector: 'div[deskRouteView]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'route' },
  template: `
    <div class="route-canvas" [style.height.px]="l().height">
      <svg [attr.width]="l().width" [attr.height]="l().height" aria-hidden="true" class="route-svg">
        @for (p of l().pieces; track $index) {
          <path [attr.d]="p.d" fill="none" [attr.stroke]="p.live ? '#2F5BD3' : '#1C1B18'" [attr.stroke-width]="p.live ? 2.5 : 2" />
        }
        @if (l().tailPath; as tail) {
          <path [attr.d]="tail" fill="none" stroke="#8A857B" stroke-width="2" stroke-dasharray="4 5" />
        }
      </svg>
      @for (p of points(); track p.stop.n) {
        <div>
          <button
            type="button"
            class="route-stop"
            [class]="'route-stop-' + p.stop.kind"
            [class.live]="p.stop.live"
            [class.muted]="p.t.muted"
            [class.selected]="selected() === p.stop.n"
            [style.left.px]="p.x"
            [style.top.px]="p.y"
            [style.width.px]="p.r * 2"
            [style.height.px]="p.r * 2"
            [attr.aria-label]="p.label"
            [attr.aria-pressed]="selected() === p.stop.n"
            (click)="selectStop.emit(p.stop.n)"
          ><span aria-hidden="true">{{ p.inner }}</span></button>
          <span class="route-num" aria-hidden="true" [style.left.px]="p.x + p.r * 0.8" [style.top.px]="p.y - p.r * 0.8">{{ p.stop.n }}</span>
          @if (p.stop.cards.length) {
            <span class="route-cards" aria-hidden="true" [style.left.px]="p.x + p.r * 0.85" [style.top.px]="p.y + p.r * 0.8">{{ p.stop.cards.length }} ✉</span>
          }
          <div class="route-label" [class.above]="p.above" [style.left.px]="p.x" [style.top.px]="p.above ? p.y - p.r - 6 : p.y + p.r + 6">
            <span class="route-label-title" [class.muted]="p.t.muted">@if (p.stop.kind === 'answer' && p.stop.live) {<span class="live-dot" aria-hidden="true"></span>}{{ p.t.title }}</span>
            @if (p.t.sub) {
              <span class="route-label-sub">{{ p.t.sub }}</span>
            }
            @if (p.t.quote) {
              <span class="route-label-quote">“{{ p.t.quote }}”</span>
            }
          </div>
        </div>
      }
      @if (l().now; as now) {
        <span class="route-now" aria-hidden="true" [style.left.px]="now.x" [style.top.px]="now.y"></span>
        <div class="route-label" [style.left.px]="now.x" [style.top.px]="now.y + 20">
          <span class="route-label-title run">{{ nowTitle() }}</span>
          @if (activity()) {
            <span class="route-label-sub mono">{{ nowSub() }}</span>
          }
        </div>
      }
      @for (t of l().tail; track $index) {
        <div>
          <span class="route-next" aria-hidden="true" [style.left.px]="t.x" [style.top.px]="t.y"></span>
          <div class="route-label" [style.left.px]="t.x" [style.top.px]="t.y + 18">
            <span class="route-label-sub">Next: report to Desk</span>
          </div>
        </div>
      }
    </div>
    <div class="route-legend">
      <span><span class="route-legend-line"></span>Route taken</span>
      <span><span class="route-legend-line live"></span>Live since sent back</span>
      <span><span class="route-legend-line next"></span>Next</span>
      <span>Detour = continued on the fallback model</span>
      <strong>Numbers match the transcript</strong>
    </div>
  `,
})
export class RouteView {
  readonly stops = input.required<Stop[]>();
  readonly running = input.required<boolean>();
  readonly activity = input<string | null>(null);
  readonly reviewRounds = input.required<number>();
  /** The session's message fold: names senders and titles answer runs. */
  readonly messages = input.required<MessagesState>();
  readonly selected = input<number | null>(null);
  /** React's `onSelect`: the stop the user clicked. */
  readonly selectStop = output<number>();
  private readonly host: HTMLElement = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly width = injectWidth(() => this.host, 900);
  protected readonly l = computed(() => routeLayout(this.stops(), Math.max(520, this.width()), this.running()));
  protected readonly points = computed<PointView[]>(() => {
    const m = this.messages();
    const rounds = this.reviewRounds();
    return this.l().points.map((p) => {
      const t = stopText(p.stop, rounds, m);
      const cards = p.stop.cards.length;
      return {
        stop: p.stop,
        x: p.x,
        y: p.y,
        r: p.r,
        t,
        label: `Stop ${p.stop.n}: ${t.title}${t.sub ? `, ${t.sub}` : ''}${cards ? `, ${plural(cards, 'message')}` : ''}`,
        inner: INNER[p.stop.kind](p.stop, m),
        above: p.stop.kind === 'detour',
      };
    });
  });
  protected readonly nowTitle = computed(() => {
    const a = this.activity();
    return `Now${a ? ` · ${a.split(' ')[0]}` : ''}`;
  });
  protected readonly nowSub = computed(() => (this.activity() ?? '').split(' ').slice(1).join(' '));
}
