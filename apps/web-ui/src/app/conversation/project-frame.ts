import { ChangeDetectionStrategy, Component, ElementRef, ViewEncapsulation, computed, inject, input, signal } from '@angular/core';
import { lineGeometry } from '@desk/ui-core';
import { PairSheet } from '../components/pair-sheet';
import { GlobalStore } from '../core/global.store';
import { NowService } from '../core/now.service';
import { RouteService } from '../core/route.service';
import { injectSession } from '../core/session.service';
import { injectWidth } from '../core/width';
import { LineDiagram, type StationG } from './line-diagram';

/**
 * The Conversation and Threads tabs' shared frame (ProjectFrame.tsx): the timeline on top and the tab below. The
 * conversation shows Desk's line alone, so the chat gets the height; Threads shows every lane. App keeps the frame across
 * the two tabs (keyed by project), so switching folds the lanes into Desk's line or unfolds them out of it.
 *
 * React wraps the diagram in an error boundary of its own. The web's boundaries are not told where an error came from
 * (the innermost registered one takes it, W0c.11), so a boundary here would take the screen's errors too: a diagram error
 * lands in the screen's boundary instead.
 */
@Component({
  selector: 'div[deskProjectFrame]',
  imports: [LineDiagram, PairSheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'project-frame' },
  template: `
    @let s = session();
    @if (s.status === 'ready' && s.project) {
      <section
        deskLineDiagram
        [g]="geometry()"
        [project]="s.project"
        [messages]="s.messages"
        [attention]="projectAttention()"
        [now]="now()"
        [mode]="mode()"
        [focus]="focus()"
        (station)="onStation($event)"
        (pair)="pairOf.set($event)"
      ></section>
    }
    <div class="project-frame-body"><ng-content /></div>
    <!-- The pair sheet's two agents (design spec §8 item 8): local state, no route. -->
    @if (pairOf(); as pair) {
      <div deskPairSheet [projectId]="projectId()" [messages]="s.messages" [a]="pair[0]" [b]="pair[1]" (close)="pairOf.set(null)"></div>
    }
  `,
})
export class ProjectFrame {
  readonly projectId = input.required<string>();
  /** `desk`: Desk's line and its stops; `full`: every lane, message link and mark. */
  readonly mode = input.required<'desk' | 'full'>();
  /** The thread open on the Threads tab: its lane stays lit, the others dim. */
  readonly focus = input<string | null>(null);
  private readonly routes = inject(RouteService);
  private readonly global = inject(GlobalStore);
  private readonly host: HTMLElement = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  protected readonly session = injectSession(this.projectId);
  protected readonly now = inject(NowService).now;
  /** The frame's width, which the diagram's geometry follows (1200 until measured, as in the desktop). */
  private readonly width = injectWidth(() => this.host);
  /** The pair sheet's two agents (design spec §8 item 8): local state, no route. */
  protected readonly pairOf = signal<readonly [string, string] | null>(null);

  private readonly attention = computed(() => this.global.state().attention);
  protected readonly projectAttention = computed(() => this.attention().filter((i) => i.project_id === this.projectId()));
  private readonly timeline = computed(() => this.session().timeline);
  private readonly threads = computed(() => this.session().project?.threads);
  private readonly messages = computed(() => this.session().messages);
  private readonly answeringRuns = computed(() => this.messages().answering);
  // A finished lane that is answering gets a stub (design spec §8 item 10); the set changes only when an answer run starts or ends.
  private readonly answeringIds = computed(() => new Set(Object.keys(this.answeringRuns())));
  protected readonly geometry = computed(() =>
    lineGeometry({ timeline: this.timeline(), threads: this.threads() ?? [], now: this.now(), width: this.width(), answering: this.answeringIds(), messages: this.messages() }),
  );

  /** A Desk stop opens the chat at that point: in place on the conversation, as a new page from Threads (Back returns). */
  protected onStation(st: StationG): void {
    const to = { name: 'project', id: this.projectId(), tab: 'conversation', at: st.eventId } as const;
    if (this.mode() === 'desk') this.routes.replace(to);
    else this.routes.navigate(to);
  }
}
