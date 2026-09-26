import { ChangeDetectionStrategy, Component, ElementRef, ViewEncapsulation, afterRenderEffect, computed, input, output, signal, viewChild } from '@angular/core';
import { messageById, type MessagesState, type ProjectState } from '@desk/client';
import type { AgentStatus, AttentionItem } from '@desk/protocol';
import { ago, answeringLabel, clock, duration, href, waitHop, waitLabel, type LaneGeometry, type LineGeometry } from '@desk/ui-core';
import { AnsweringBadge } from '../components/answering-badge';

/** A station on Desk's trunk, positioned. */
export type StationG = LineGeometry['stations'][number];

/** A lane label's second line. `wait`: what a waiting thread waits on, from the message fold (waitLabel). */
function laneStatus(l: LaneGeometry, reviewRounds: number, wait: string | null, now: number): { text: string; tone: AgentStatus } {
  const t = l.thread;
  const status = t?.status ?? l.lane.status;
  const since = ago(l.lane.forkedAt, now);
  switch (status) {
    case 'running':
      return { text: t?.review_round ? `revision ${t.review_round} of ${reviewRounds} · ${since}` : `running · ${since}`, tone: status };
    case 'waiting':
      return { text: wait ?? `waiting · ${since}`, tone: status };
    case 'queued':
      return { text: /restart/i.test(t?.reason ?? '') ? 'will resume' : 'queued', tone: status };
    case 'done': {
      const last = l.lane.segments.at(-1);
      return { text: `done · ${duration(Date.parse(last?.from ?? l.lane.forkedAt) - Date.parse(l.lane.forkedAt))}`, tone: status };
    }
    default:
      return { text: status, tone: status };
  }
}

const shortModel = (m: string) => m.replace(/^claude-/, '');

/** The hump a rate-limit detour draws over its lane (a halo, then the lane's colour). */
const detourPath = (x: number, y: number) => `M${x - 16} ${y} C${x - 8} ${y} ${x - 8} ${y - 12} ${x} ${y - 12} C${x + 8} ${y - 12} ${x + 8} ${y} ${x + 16} ${y}`;

/** A tracked question on its asker's lane; its state is the fold's. */
type QuestionMark = { key: string; from: string; to: string | null; state: string; x: number; y: number; aria: string; title: string };

/**
 * The transit diagram: Desk's trunk with stations, thread lanes forking and rejoining, trains at "now".
 * The lanes scroll sideways (older history to the left) and follow "now" unless the user has scrolled back;
 * the label column and the legend stay put. Every position comes from lineGeometry.
 */
@Component({
  selector: 'section[deskLineDiagram]',
  imports: [AnsweringBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'line-diagram', 'aria-label': 'Line diagram: Desk and its threads since the brief', '[style.height.px]': 'g().height' },
  template: `
    @let geo = g();
    @let desk = project().desk;
    <div class="line-scroll" #scroller [style.left.px]="geo.viewportLeft" [style.width.px]="geo.viewportWidth" [style.height.px]="geo.height" (scroll)="onScroll(scroller)">
      <div class="line-canvas" [style.width.px]="geo.contentWidth" [style.height.px]="geo.height">
        <svg [attr.width]="geo.contentWidth" [attr.height]="geo.height" aria-hidden="true" class="line-svg">
          @for (t of geo.ticks; track t.t) {
            <path [attr.d]="'M' + t.x + ' 24 V' + geo.height" stroke="#E3DFD6" stroke-dasharray="2 4" />
          }
          <path [attr.d]="'M' + geo.nowX + ' 22 V' + geo.height" stroke="#4A4740" stroke-dasharray="3 3" />
          @for (l of geo.lanes; track l.lane.threadId) {
            <g [attr.opacity]="l.lane.archived ? 0.45 : 1">
              <path [attr.d]="l.fork" fill="none" [attr.stroke]="l.forkColor" stroke-width="4" stroke-linecap="round" />
              @for (s of l.segments; track $index) {
                <path [attr.d]="s.d" fill="none" [attr.stroke]="s.color" stroke-width="4" stroke-linecap="round" [attr.stroke-dasharray]="s.dashed ? '3 5' : null" />
              }
              @for (d of l.rejoins; track $index) {
                <path [attr.d]="d" fill="none" [attr.stroke]="l.rejoinColor" stroke-width="4" stroke-linecap="round" />
              }
              @if (l.stub; as stub) {
                <path class="line-stub" [attr.d]="stub.d" fill="none" stroke="#8A857B" stroke-width="3" stroke-linecap="round" stroke-dasharray="1 5" />
              }
              @for (m of l.marks; track $index) {
                @if (m.kind === 'detour') {
                  <g>
                    <path [attr.d]="detour(m.x, l.y)" fill="none" stroke="#EFEAE0" stroke-width="9" />
                    <path [attr.d]="detour(m.x, l.y)" fill="none" [attr.stroke]="l.color" stroke-width="4" stroke-linecap="round" />
                  </g>
                }
              }
            </g>
          }
          <path [attr.d]="'M' + (geo.trunkStart - 6) + ' ' + geo.trunkY + ' H' + geo.nowX" stroke="#1C1B18" stroke-width="6" stroke-linecap="round" />
        </svg>

        @for (t of geo.ticks; track t.t) {
          <span class="line-tick" [style.left.px]="t.x">{{ clock(t.t) }}</span>
        }
        <span class="line-now" [style.left.px]="geo.nowX">now {{ clock(now()) }}</span>

        @for (st of geo.stations; track st.eventId) {
          <div>
            <button
              type="button"
              class="line-station"
              [class]="'line-station-' + st.kind"
              [style.left.px]="st.x"
              [style.top.px]="geo.trunkY"
              [attr.aria-label]="clock(st.ts) + ', ' + st.label"
              [title]="clock(st.ts) + ' · ' + st.label"
              (click)="station.emit(st)"
            ></button>
            @if (st.showLabel) {
              <span class="line-station-label" [class]="stationLabelEdge(st)" [style.left.px]="st.x" [style.top.px]="geo.trunkY - 31"><span class="mono muted">{{ clock(st.ts) }}</span> {{ st.kind === 'brief' ? 'Your brief · ' + st.label : st.label }}</span>
            }
          </div>
        }

        @for (l of geo.lanes; track l.lane.threadId) {
          @for (m of l.marks; track $index) {
            @if (m.kind === 'detour' || m.kind === 'sent_back' || m.kind === 'stalled') {
              <span class="line-mark" [class]="'line-mark-' + m.kind" [style.left.px]="m.x" [style.top.px]="m.kind === 'detour' ? l.y + 8 : l.y - 24">
                @switch (m.kind) {
                  @case ('detour') {
                    <span class="mono">{{ clock(m.ts) }} {{ shortModel(m.label) }}</span><span class="sr-only">: rate limited, continued on the fallback model</span>
                  }
                  @case ('sent_back') {
                    <ng-container>sent back · {{ m.label }}</ng-container>
                  }
                  @default {
                    <ng-container>stalled</ng-container>
                  }
                }
              </span>
            }
          }
        }

        @for (q of questionMarks(); track q.key) {
          <button
            type="button"
            class="line-q"
            [class]="'line-q-' + q.state"
            [style.left.px]="q.x"
            [style.top.px]="q.y"
            [attr.aria-label]="q.aria"
            [title]="q.title"
            (click)="openQuestion(q)"
            (mouseenter)="lit.set(q.to)"
            (mouseleave)="lit.set(null)"
            (focus)="lit.set(q.to)"
            (blur)="lit.set(null)"
          ></button>
        }

        @for (l of geo.lanes; track l.lane.threadId) {
          @if (l.stub; as stub) {
            <span class="line-answering" [style.left.px]="stub.x" [style.top.px]="l.y" aria-hidden="true"><span class="live-dot"></span></span>
          }
        }

        @for (sig of signals(); track sig.threadId) {
          <a class="line-signal" [style.left.px]="sig.x" [style.top.px]="sig.y" [href]="sig.href">
            <span class="line-signal-chip"><strong>Waiting for your approval</strong> · <span class="mono">{{ sig.label }}</span> · <span class="mono muted">{{ clock(sig.ts) }}</span></span>
            <span class="line-signal-dot" aria-hidden="true"></span>
          </a>
        }

        <!-- A waiting lane whose wait leads to something that needs the user ends in a vermilion dot linking to it. -->
        @for (h of hops(); track h.threadId) {
          <a class="line-hop" [style.left.px]="geo.nowX" [style.top.px]="h.y" [href]="h.href" [attr.aria-label]="h.label" [title]="h.title"></a>
        }

        @for (l of geo.lanes; track l.lane.threadId) {
          @if (l.trainX !== null) {
            <div>
              @if (l.thread?.activity) {
                <span class="line-activity" [style.left.px]="l.trainX - 18" [style.top.px]="l.y - 26">{{ l.thread.activity }}</span>
              }
              <a class="line-train" [style.left.px]="l.trainX" [style.top.px]="l.y" [href]="threadHref(l.lane.threadId)" [attr.aria-label]="l.lane.title + ', running now'"><span aria-hidden="true"></span></a>
            </div>
          }
        }

        @for (l of geo.lanes; track l.lane.threadId) {
          @if (l.inlineLabel; as inline) {
            <a
              class="line-lane-title"
              [class.lit]="lit() === l.lane.threadId"
              [style.left.px]="inline.x"
              [style.top.px]="l.y - 20"
              [style.max-width.px]="inline.width"
              [href]="threadHref(l.lane.threadId)"
            >{{ l.lane.title }}</a>
          }
        }

        @if (desk?.status === 'running') {
          <span class="line-desk-writing" [style.left.px]="geo.nowX - 10" [style.top.px]="geo.trunkY"><span class="live-dot" aria-hidden="true"></span>Desk · writing</span>
        }
      </div>
    </div>

    <div class="line-label" [class.lit]="lit() !== null && lit() === desk?.id" [style.top.px]="geo.trunkY - 15">
      <span class="line-label-title"><span class="line-swatch line-swatch-desk"></span>Desk</span>
      <span class="line-label-sub" [class]="'status-text-' + (desk?.status ?? 'idle')">{{ deskSub() }}</span>
    </div>
    @for (r of rowLabels(); track r.id) {
      <!-- An answer run keeps the thread's status: the label says it is answering (design spec §8 item 3). -->
      <a class="line-label" [class.lit]="lit() === r.id" [style.top.px]="r.y - 15" [href]="r.href">
        <span class="line-label-title"><span class="line-swatch" [style.background]="r.color"></span>{{ r.title }}</span>
        <span class="line-label-sub" [class]="'status-text-' + r.tone">@if (r.answering; as answering) {<ng-container>{{ r.tone }} · </ng-container><span deskAnsweringBadge [label]="answering"></span>} @else {<ng-container>{{ r.text }}</ng-container>}</span>
      </a>
    }

    <div class="line-legend" aria-hidden="true">
      <span><span class="line-swatch line-swatch-desk"></span>Desk</span>
      <span><span class="line-swatch" style="background: #2F5BD3"></span>running</span>
      <span><span class="line-swatch" style="background: #8A857B"></span>done</span>
      <span><span class="line-swatch" style="background: #A15C00"></span>waiting</span>
      <span><span class="line-legend-dot"></span>needs you</span>
      @if (hasQuestions()) {
        <span><span class="line-legend-q"></span>question</span>
      }
      <span><svg width="18" height="10" viewBox="0 0 18 10"><path d="M1 8 H4 C6 8 6 2 9 2 C12 2 12 8 14 8 H17" fill="none" stroke="#2F5BD3" stroke-width="2" stroke-linecap="round" /></svg>fallback</span>
    </div>
  `,
})
export class LineDiagram {
  readonly g = input.required<LineGeometry>();
  readonly project = input.required<ProjectState>();
  /** The session's message fold: what waiting threads wait on, and which threads are answering. */
  readonly messages = input.required<MessagesState>();
  /** The project's attention items: only a thread with one is "waiting on you". */
  readonly attention = input.required<readonly AttentionItem[]>();
  readonly now = input.required<number>();
  /** A station was pressed: the chat jumps to it. */
  readonly station = output<StationG>();
  /** Opens the pair sheet of two agents: a question mark's asker and its recipient (design spec §8 item 8). */
  readonly pair = output<readonly [string, string]>();

  /** The counterpart of the question mark under the pointer or focus: its label lights up. */
  protected readonly lit = signal<string | null>(null);
  protected readonly clock = clock;
  protected readonly shortModel = shortModel;
  protected readonly detour = detourPath;
  private readonly scroller = viewChild.required<ElementRef<HTMLDivElement>>('scroller');
  /** Whether the view follows "now" (the user hasn't scrolled back into history). */
  private pinned = true;
  /** The two numbers the scroll follows; other geometry changes do not move the view. */
  private readonly extent = computed(() => ({ width: this.g().contentWidth, now: this.g().nowX }), { equal: (a, b) => a.width === b.width && a.now === b.now });

  protected readonly hasQuestions = computed(() => this.g().lanes.some((l) => l.marks.some((m) => m.kind === 'question')));

  protected readonly questionMarks = computed((): QuestionMark[] => {
    const m = this.messages();
    return this.g().lanes.flatMap((l) =>
      l.marks
        .filter((mark) => mark.kind === 'question')
        .map((mark) => {
          const message = messageById(m, mark.eventId);
          const state = message?.state ?? 'open';
          return {
            key: `q-${mark.eventId}`,
            from: l.lane.threadId,
            to: message?.to ?? null,
            state,
            x: mark.x,
            y: l.y,
            aria: `${l.lane.title} asked ${mark.label}, ${clock(mark.ts)}`,
            title: `${l.lane.title} asked ${mark.label} · ${clock(mark.ts)} · ${state}`,
          };
        }),
    );
  });

  protected readonly signals = computed(() => {
    const p = this.project();
    return this.g().lanes.flatMap((l) => {
      if (!l.signal) return [];
      const approval = p.approvals.find((a) => a.agent_id === l.lane.threadId && !a.delegate_to_desk);
      return [{ threadId: l.lane.threadId, x: l.signal.x, y: l.y, label: l.signal.label, ts: l.signal.ts, href: href({ name: 'attention', ...(approval ? { item: `approval:${approval.id}` } : {}) }) }];
    });
  });

  protected readonly hops = computed(() => {
    const m = this.messages();
    const attention = this.attention();
    return this.g().lanes.flatMap((l) => {
      const hop = (l.thread?.status ?? l.lane.status) === 'waiting' ? waitHop(m, l.lane.threadId, attention) : null;
      return hop ? [{ threadId: l.lane.threadId, y: l.y, href: href({ name: 'attention', item: hop.item.id }), label: hop.label, title: `${l.lane.title} waits on it: ${hop.label}` }] : [];
    });
  });

  protected readonly rowLabels = computed(() => {
    const m = this.messages();
    const attention = this.attention();
    const now = this.now();
    const rounds = this.project().project.settings.review_rounds;
    return this.g().rows.map((l) => {
      const id = l.lane.threadId;
      const waiting = (l.thread?.status ?? l.lane.status) === 'waiting';
      const wait = waiting ? waitLabel(m, id, attention, now) : null;
      // One hop further when what it waits on needs the user (design spec §8 item 11); the dot at the lane's end links there.
      const hop = wait ? waitHop(m, id, attention) : null;
      const s = laneStatus(l, rounds, wait && hop ? `${wait} ${hop.text}` : wait, now);
      return { id, y: l.y, color: l.color, title: l.lane.title, href: this.threadHref(id), text: s.text, tone: s.tone, answering: answeringLabel(m, id) };
    });
  });

  protected readonly deskSub = computed(() => {
    const desk = this.project().desk;
    return desk ? `${desk.status === 'running' ? 'writing' : desk.status} · ${shortModel(desk.model_override ?? desk.model)}` : '';
  });

  constructor() {
    afterRenderEffect(() => {
      this.extent();
      const el = this.scroller().nativeElement;
      if (this.pinned) el.scrollLeft = el.scrollWidth;
    });
  }

  protected threadHref(threadId: string): string {
    return href({ name: 'project', id: this.project().project.id, tab: 'threads', threadId });
  }

  /** The station label's edge class ('end' or 'start'), which keeps a label near either end inside the diagram. */
  protected stationLabelEdge(st: StationG): string {
    return st.x > this.g().x1 - 260 ? 'end' : st.x < 120 ? 'start' : '';
  }

  protected onScroll(el: HTMLElement): void {
    this.pinned = el.scrollWidth - el.scrollLeft - el.clientWidth < 8;
  }

  protected openQuestion(q: QuestionMark): void {
    if (q.to) this.pair.emit([q.from, q.to]);
  }
}
