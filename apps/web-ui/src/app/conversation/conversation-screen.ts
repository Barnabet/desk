import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  ViewEncapsulation,
  afterNextRender,
  afterRenderEffect,
  computed,
  effect,
  inject,
  input,
  linkedSignal,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import type { ProjectState } from '@desk/client';
import { chatEventId, keepStable, lineGeometry, plural, rowViews, ticks, type RowView } from '@desk/ui-core';
import { EmptyState } from '../components/empty-state';
import { PairSheet } from '../components/pair-sheet';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { injectMediaQuery } from '../core/media';
import { NowService } from '../core/now.service';
import { injectSession } from '../core/session.service';
import { Unread } from '../core/unread';
import { injectWidth } from '../core/width';
import { ChatItemView, chatDomId } from './chat-items';
import { Composer } from './composer';
import { LineDiagram, type StationG } from './line-diagram';
import { PlanPanel } from './plan-panel';
import { ServicesCard } from './services-card';
import { WhatsUp } from './whats-up';

/** Chat items rendered at first; scrolling up renders this many more (long conversations stay fast). */
export const CHAT_PAGE = 60;

const draftKey = (projectId: string) => `desk.draft.${projectId}`;

function readDraft(projectId: string): string {
  try {
    return localStorage.getItem(draftKey(projectId)) ?? '';
  } catch {
    return '';
  }
}

/**
 * A project's conversation: the line diagram, What's up, the chat with Desk and its composer, the plan and Desk's card,
 * the services, and the pair sheet. The host is the React screen's root: `div.conversation` once the project is loaded,
 * `div.page` while loading or when it cannot be shown.
 */
@Component({
  selector: 'div[deskConversationScreen]',
  imports: [EmptyState, LineDiagram, WhatsUp, ServicesCard, ChatItemView, Composer, PlanPanel, PairSheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { '[class]': 'hostClass()' },
  template: `
    @let s = session();
    @if (s.status === 'loading') {
      <ng-container>Loading…</ng-container>
    } @else if (s.status === 'missing') {
      <div deskEmptyState title="This project isn't here" [body]="'It may have been archived.'"><a href="#/map">Back to the map</a></div>
    } @else if (s.status === 'error' || !s.project) {
      <div deskEmptyState title="Couldn't load this project" [body]="s.error"></div>
    } @else {
      @let project = s.project;
      <section deskLineDiagram [g]="geometry()" [project]="project" [messages]="s.messages" [attention]="projectAttention()" [now]="now()" (station)="onStation($event)" (pair)="pairOf.set($event)"></section>
      <div class="conv-body" [class.plan-open]="planOpen()">
        <div class="conv-intro">
          <section deskWhatsUp [project]="project" [now]="now()"></section>
          <button type="button" class="btn btn-secondary btn-sm plan-toggle" [attr.aria-expanded]="planOpen()" (click)="planOpen.set(!planOpen())">Plan{{ servicesNote(project) }}</button>
          <!-- Services sit at the bottom of the left column; on narrow windows that column collapses, so they join the plan overlay. -->
          @if (!narrow() && project.services.length) {
            <section deskServicesCard [project]="project"></section>
          }
        </div>
        <section class="conv-chat" aria-label="Conversation with Desk">
          <div class="chat-list" #list (scroll)="onScroll(list)">
            @if (start() > 0) {
              <button type="button" class="btn btn-ghost btn-sm chat-earlier" (click)="showEarlier()">Show earlier messages ({{ start() }})</button>
            }
            @if (items().length === 0 && !pending().length) {
              <div deskEmptyState title="Brief Desk" [body]="'Say what you want done. Desk plans it, splits it into threads, and reports back.'"></div>
            }
            @for (item of shownItems(); track item.id) {
              @let v = views().get(item.id);
              <div
                deskChatItem
                [item]="item"
                [projectId]="projectId()"
                [attentionIds]="attentionIds()"
                [answering]="answering()"
                [view]="v"
                [now]="ticks(v) ? now() : undefined"
                [deskId]="s.chat.agentId"
                (answer)="answer($event)"
                (ownWords)="focusComposer()"
                (jump)="jumpToItem($event)"
                (pair)="pairOf.set($event)"
              ></div>
            }
            @for (text of pending(); track $index) {
              <div class="chat-item"><div class="chat-user pending"><span class="chat-meta">You · sending…</span><p>{{ text }}</p></div></div>
            }
          </div>
          <div deskComposer [projectId]="projectId()" [(draft)]="draft" (sent)="addPending($event)"></div>
        </section>
        <div class="conv-side">
          <aside deskPlanPanel [project]="project" [proxyDown]="proxyDown()" [attention]="projectAttention()"></aside>
          @if (narrow() && project.services.length) {
            <section deskServicesCard [project]="project"></section>
          }
        </div>
      </div>
      <!-- The pair sheet's two agents (design spec §8 item 8): local state, no route. -->
      @if (pairOf(); as pair) {
        <div deskPairSheet [projectId]="projectId()" [messages]="s.messages" [a]="pair[0]" [b]="pair[1]" (close)="pairOf.set(null)"></div>
      }
    }
  `,
})
export class ConversationScreen {
  readonly projectId = input.required<string>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly global = inject(GlobalStore);
  private readonly unread = inject(Unread);
  private readonly injector = inject(Injector);
  private readonly host: HTMLElement = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  protected readonly session = injectSession(this.projectId);
  protected readonly now = inject(NowService).now;
  // Services sit at the bottom of the left column; on narrow windows that column collapses, so they join the plan overlay.
  protected readonly narrow = injectMediaQuery('(max-width: 1279px)');
  /** The conversation's width, which the line diagram's geometry follows (1200 until measured, as in the desktop). */
  private readonly width = injectWidth(() => this.host);
  /** Saved per project in this browser (`desk.draft.<id>`); drafts are a convenience. */
  protected readonly draft = linkedSignal(() => readDraft(this.projectId()));
  /** Messages sent but not yet back as events: "You · sending…". */
  protected readonly pending = signal<string[]>([]);
  /** The option of Desk's question being sent. */
  protected readonly answering = signal<string | null>(null);
  protected readonly planOpen = signal(false);
  protected readonly shown = signal(CHAT_PAGE);
  /** The pair sheet's two agents (design spec §8 item 8): local state, no route. */
  protected readonly pairOf = signal<readonly [string, string] | null>(null);
  protected readonly ticks = ticks;
  private readonly list = viewChild<ElementRef<HTMLDivElement>>('list');
  private readonly composer = viewChild(Composer);
  /** Whether the chat follows its newest item (the user hasn't scrolled up). */
  private pinned = true;
  /** Scroll position to keep while older items are rendered above it. */
  private anchor: { height: number; top: number } | null = null;
  /** Last render's row views: a row whose view did not change keeps its object, so its inputs do not change (design spec §7). */
  private lastViews: ReadonlyMap<string, RowView> = new Map();

  protected readonly items = computed(() => this.session().chat.items);
  private readonly messages = computed(() => this.session().messages);
  protected readonly views = computed(() => (this.lastViews = keepStable(this.lastViews, rowViews(this.items(), this.messages()))));
  private readonly attention = computed(() => this.global.state().attention);
  protected readonly projectAttention = computed(() => this.attention().filter((i) => i.project_id === this.projectId()));
  protected readonly attentionIds = computed(() => new Set(this.projectAttention().map((i) => i.id)));
  protected readonly proxyDown = computed(() => this.global.state().system.proxy === 'down');
  private readonly timeline = computed(() => this.session().timeline);
  private readonly threads = computed(() => this.session().project?.threads);
  private readonly answeringRuns = computed(() => this.messages().answering);
  // A finished lane that is answering gets a stub (design spec §8 item 10); the set changes only when an answer run starts or ends.
  private readonly answeringIds = computed(() => new Set(Object.keys(this.answeringRuns())));
  protected readonly geometry = computed(() => lineGeometry({ timeline: this.timeline(), threads: this.threads() ?? [], now: this.now(), width: this.width(), answering: this.answeringIds() }));
  protected readonly start = computed(() => Math.max(0, this.items().length - this.shown()));
  protected readonly shownItems = computed(() => {
    const start = this.start();
    return start ? this.items().slice(start) : this.items();
  });
  private readonly eventCount = computed(() => this.session().events.length);
  protected readonly hostClass = computed(() => {
    const s = this.session();
    if (s.status === 'loading') return 'page muted';
    return s.status === 'ready' && s.project ? 'conversation' : 'page';
  });

  constructor() {
    effect(() => {
      const id = this.projectId();
      this.eventCount();
      untracked(() => this.unread.markSeen(id));
    });
    effect(() => {
      const key = draftKey(this.projectId());
      const draft = this.draft();
      try {
        if (draft) localStorage.setItem(key, draft);
        else localStorage.removeItem(key);
      } catch {
        // Drafts are a convenience.
      }
    });
    // A pending send is done once its message is among the latest user messages.
    effect(() => {
      const pending = this.pending();
      if (!pending.length) return;
      const sent = new Set(this.items().flatMap((i) => (i.kind === 'user' ? [i.text] : [])).slice(-20));
      const left = pending.filter((t) => !sent.has(t));
      if (left.length !== pending.length) this.pending.set(left);
    });
    afterRenderEffect(() => {
      this.items();
      this.pending();
      const el = this.list()?.nativeElement;
      if (el && this.pinned) el.scrollTop = el.scrollHeight;
    });
    afterRenderEffect(() => {
      this.shown();
      const el = this.list()?.nativeElement;
      const anchor = this.anchor;
      if (!el || !anchor) return;
      el.scrollTop = el.scrollHeight - anchor.height + anchor.top;
      this.anchor = null;
    });
  }

  protected servicesNote(project: ProjectState): string {
    const running = project.services.filter((x) => x.status === 'running').length;
    return running ? ` · ${plural(running, 'service')}` : '';
  }

  protected async answer(text: string): Promise<void> {
    this.answering.set(text);
    try {
      await this.bridge.call('projects.send', { id: this.projectId(), text });
      this.pending.update((p) => [...p, text]);
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.answering.set(null);
    }
  }

  protected addPending(text: string): void {
    this.pending.update((p) => [...p, text]);
  }

  protected focusComposer(): void {
    this.composer()?.focus();
  }

  protected onScroll(el: HTMLElement): void {
    this.pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (el.scrollTop < 400 && this.start() > 0 && !this.anchor) this.showEarlier();
  }

  protected showEarlier(): void {
    const el = this.list()?.nativeElement;
    if (el) this.anchor = { height: el.scrollHeight, top: el.scrollTop };
    this.shown.update((n) => n + CHAT_PAGE);
  }

  protected onStation(st: StationG): void {
    this.jumpToIndex(this.items().findIndex((i) => chatEventId(i) >= st.eventId));
  }

  protected jumpToItem(itemId: string): void {
    this.jumpToIndex(this.items().findIndex((i) => i.id === itemId));
  }

  /** Scrolls to the item at `index` (rendering older items first when it is not shown yet) and flashes it. */
  private jumpToIndex(index: number): void {
    const item = this.items()[index];
    if (!item) return;
    this.pinned = false;
    if (index < this.start()) this.shown.set(this.items().length - index + 5);
    afterNextRender(
      () => {
        const el = document.getElementById(chatDomId(item.id));
        el?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
        el?.classList.add('flash');
        setTimeout(() => el?.classList.remove('flash'), 1200);
      },
      { injector: this.injector },
    );
  }
}
