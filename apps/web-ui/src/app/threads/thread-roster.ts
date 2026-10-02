import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, signal } from '@angular/core';
import { waitingOn, type MessagesState, type ProjectState, type ThreadView } from '@desk/client';
import { acceptanceChip, ago, answeringLabel, href, waitHop, waitLabel, waitsOnYou, type ReviewChip, type WaitHop } from '@desk/ui-core';
import { AnsweringBadge } from '../components/answering-badge';
import { EmptyState } from '../components/empty-state';
import { HopLink } from '../components/hop-link';
import { PairSheet } from '../components/pair-sheet';
import { SkillBadge } from '../components/skill-badge';
import { StatusChip } from '../components/status-chip';
import { TemplateOf, templateOf } from '../components/template-of';
import { GlobalStore } from '../core/global.store';

/** Busiest first. A Map, so a status it does not know (even `constructor`) sorts last instead of reading `Object.prototype`. */
const ORDER = new Map<string, number>([['waiting', 0], ['running', 1], ['queued', 2], ['idle', 3], ['failed', 4], ['done', 5], ['cancelled', 6]]);

/** One card: the React ThreadCard's props. */
type RosterCard = {
  t: ThreadView;
  href: string;
  /** "answering Desk" while the thread's answer run is in progress; its status chip does not change. */
  answering: string | null;
  /** Where its latest submission stands, or Reviewer (reviews and acceptance spec §7). */
  chip: ReviewChip | null;
  /** What a waiting thread waits on (waitLabel), shown as its own line (design spec §8 item 13). */
  wait: string | null;
  /** One hop further, to what needs the user. */
  hop: WaitHop | null;
  /** The agent the pair sheet opens with; null for "waiting on you", which is not a pair. */
  target: string | null;
};

/** Every thread in the project as cards, busiest first. */
@Component({
  selector: 'div[deskThreadRoster]',
  imports: [NgTemplateOutlet, TemplateOf, AnsweringBadge, EmptyState, HopLink, PairSheet, SkillBadge, StatusChip],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'page roster' },
  template: `
    <!-- ThreadCard: the card's link. A waiting thread's wait sits in a slot under it: a button cannot live inside a link. -->
    <ng-template #card let-c [deskTemplateOf]="cardType">
      <a class="card thread-card" [class.archived]="!!c.t.archived_at" [class.has-wait]="!!c.wait" [href]="c.href">
        <div class="thread-card-head">
          <h2>{{ c.t.title ?? 'Untitled thread' }}</h2>
          <span deskStatusChip [status]="c.t.status" [reason]="c.t.reason" [proxyDown]="proxyDown()"></span>
        </div>
        @if (c.answering) {
          <span deskAnsweringBadge [label]="c.answering"></span>
        }
        @if (c.chip; as chip) {
          <span [class]="'chip chip-' + chip.tone + ' thread-acceptance'">{{ chip.label }}</span>
        }
        @if (c.t.reason && c.t.status !== 'running' && c.t.status !== 'waiting') {
          <span class="small muted">{{ c.t.reason }}</span>
        }
        @if (c.t.activity) {
          <span class="mono small thread-activity">{{ c.t.activity }}</span>
        }
        <dl class="thread-facts">
          <div>
            <dt>Elapsed</dt>
            <dd>{{ ago(c.t.created_at, now()) }}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd class="mono">{{ c.t.model_override ?? c.t.model }}@if (c.t.effort) {<span class="muted"> · {{ c.t.effort }}</span>}</dd>
          </div>
          <div>
            <dt>Workspace</dt>
            <dd class="mono">{{ c.t.git_branch ?? 'scratch' }}</dd>
          </div>
          @if (c.t.review_round) {
            <div>
              <dt>Revision</dt>
              <dd>{{ c.t.review_round }} of {{ reviewRounds() }}</dd>
            </div>
          }
        </dl>
        @if (c.t.active_skills.length) {
          <div class="thread-skills">
            @for (s of c.t.active_skills; track s) {
              <span deskSkillBadge [name]="s"></span>
            }
          </div>
        }
        @if (c.t.archived_at) {
          <span class="small muted">Archived</span>
        }
      </a>
    </ng-template>

    <div class="roster-head">
      <h1 class="title">Threads</h1>
      <span class="muted">Desk dispatches threads; message Desk in the Conversation to start new work.</span>
      <span class="grow"></span>
      @if (archived()) {
        <label class="small"><input type="checkbox" [checked]="showArchived()" (change)="toggle($event)" /> Show {{ archived() }} archived</label>
      }
    </div>
    @if (cards().length) {
      <div class="roster-grid">
        @for (c of cards(); track c.t.id) {
          @if (c.wait) {
            <div class="thread-card-slot">
              <ng-container *ngTemplateOutlet="card; context: { $implicit: c }" />
              <div class="thread-card-wait">@if (c.target) {<button type="button" class="link wait-line" (click)="pairOf.set([c.t.id, c.target])">{{ c.wait }}</button>} @else {<span class="wait-line">{{ c.wait }}</span>}@if (c.hop) {<a deskHopLink [hop]="c.hop"></a>}</div>
            </div>
          } @else {
            <ng-container *ngTemplateOutlet="card; context: { $implicit: c }" />
          }
        }
      </div>
    } @else {
      <div deskEmptyState title="No threads yet" body="Desk splits your brief into threads that work in parallel."><a [href]="conversationHref()">Brief Desk</a></div>
    }
    @if (pairOf(); as pair) {
      <div deskPairSheet [projectId]="project().project.id" [messages]="messages()" [a]="pair[0]" [b]="pair[1]" (close)="pairOf.set(null)"></div>
    }
  `,
})
export class ThreadRoster {
  readonly project = input.required<ProjectState>();
  /** The session's message fold: waits, answer runs and the pair sheet read it. */
  readonly messages = input.required<MessagesState>();
  readonly now = input.required<number>();
  private readonly global = inject(GlobalStore).state;
  protected readonly proxyDown = computed(() => this.global().system.proxy === 'down');
  private readonly attention = computed(() => this.global().attention);
  protected readonly showArchived = signal(false);
  /** The pair sheet's two agents (design spec §8 item 8): local state, no route. */
  protected readonly pairOf = signal<readonly [string, string] | null>(null);
  protected readonly reviewRounds = computed(() => this.project().project.settings.review_rounds);
  protected readonly archived = computed(() => this.project().threads.filter((t) => t.archived_at).length);
  protected readonly conversationHref = computed(() => href({ name: 'project', id: this.project().project.id, tab: 'conversation' }));
  protected readonly ago = ago;
  protected readonly cardType = templateOf<RosterCard>();

  protected readonly cards = computed<RosterCard[]>(() => {
    const projectId = this.project().project.id;
    const messages = this.messages();
    const attention = this.attention();
    const now = this.now();
    const show = this.showArchived();
    return this.project()
      .threads.filter((t) => show || !t.archived_at)
      .sort((a, b) => (ORDER.get(a.status) ?? 9) - (ORDER.get(b.status) ?? 9) || b.created_at.localeCompare(a.created_at))
      .map((t) => {
        const wait = t.status === 'waiting' ? waitLabel(messages, t.id, attention, now) : null;
        // Its first wait target, for the pair sheet; a thread with an item of its own that waits on the user waits on you,
        // which is no pair.
        const target = wait && !attention.some((i) => i.agent_id === t.id && waitsOnYou(i)) ? (waitingOn(messages, t.id, attention)[0]?.agentId ?? null) : null;
        return {
          t,
          href: href({ name: 'project', id: projectId, tab: 'threads', threadId: t.id }),
          answering: answeringLabel(messages, t.id),
          chip: acceptanceChip(t),
          wait,
          hop: wait ? waitHop(messages, t.id, attention) : null,
          target,
        };
      });
  });

  protected toggle(e: Event): void {
    this.showArchived.set((e.target as HTMLInputElement).checked);
  }
}
