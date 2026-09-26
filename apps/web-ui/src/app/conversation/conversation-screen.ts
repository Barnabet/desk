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
import { chatEventId, keepStable, plural, rowViews, ticks, type RowView } from '@desk/ui-core';
import { EmptyState } from '../components/empty-state';
import { PairSheet } from '../components/pair-sheet';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { injectMediaQuery } from '../core/media';
import { NowService } from '../core/now.service';
import { injectSession } from '../core/session.service';
import { RouteService } from '../core/route.service';
import { Unread } from '../core/unread';
import { ChatItemView, chatDomId } from './chat-items';
import { Composer } from './composer';
import { PlanPanel } from './plan-panel';
import { ServicesCard } from './services-card';
import { WhatsUp } from './whats-up';

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');

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
 * A project's conversation: What's up, the chat with Desk and its composer, the plan and Desk's card, the services, and
 * the pair sheet of the chat's rows. The line diagram is `ProjectFrame`'s, above it. The host is the React screen's root:
 * `div.conversation` once the project is loaded, `div.page` while loading or when it cannot be shown.
 */
@Component({
  selector: 'div[deskConversationScreen]',
  imports: [EmptyState, WhatsUp, ServicesCard, ChatItemView, Composer, PlanPanel, PairSheet],
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
      <div class="conv-body" [class.plan-open]="planOpen()">
        <div class="conv-intro">
          <section deskWhatsUp [project]="project" [now]="now()"></section>
          <button type="button" class="btn btn-secondary btn-sm plan-toggle" [attr.aria-expanded]="planOpen()" (click)="planOpen.set(!planOpen())">Plan{{ servicesNote(project) }}</button>
          <!-- Services sit at the bottom of the left column; on narrow windows that column collapses, so they join the plan overlay. -->
          @if (!narrow() && project.services.length) {
            <section deskServicesCard [project]="project"></section>
          }
        </div>
        <section
          #chat
          class="conv-chat"
          [class.dropping]="dropping()"
          aria-label="Conversation with Desk"
          (dragenter)="onDragEnter($event)"
          (dragleave)="onDragLeave($event)"
          (drop)="onDrop($event)"
        >
          @if (dropping()) {
            <div class="chat-drop" aria-hidden="true">Drop to attach to the Library</div>
          }
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
  /** A Desk stop's event id to scroll the chat to (a stop clicked on the timeline); dropped from the route once done. */
  readonly at = input<number | undefined>(undefined);
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly global = inject(GlobalStore);
  private readonly unread = inject(Unread);
  private readonly injector = inject(Injector);
  private readonly routes = inject(RouteService);
  protected readonly session = injectSession(this.projectId);
  protected readonly now = inject(NowService).now;
  // Services sit at the bottom of the left column; on narrow windows that column collapses, so they join the plan overlay.
  protected readonly narrow = injectMediaQuery('(max-width: 1279px)');
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
  private readonly chat = viewChild<ElementRef<HTMLElement>>('chat');
  /** Files are being dragged over the chat, which takes them all (React's `useFileDrop`). */
  protected readonly dropping = signal(false);
  /** dragenter and dragleave fire for every child crossed: files are over the chat while this is above zero. */
  private drags = 0;
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
  private readonly status = computed(() => this.session().status);
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
    // Not a template (dragover) listener: dragover fires many times a second, and each would schedule change detection.
    // Taking it is what lets the chat take the drop; the app's file-drop guard leaves a dragover taken here alone.
    effect((onCleanup) => {
      const el = this.chat()?.nativeElement;
      if (!el) return;
      const onDragOver = (e: DragEvent) => {
        if (hasFiles(e)) e.preventDefault();
      };
      el.addEventListener('dragover', onDragOver);
      onCleanup(() => el.removeEventListener('dragover', onDragOver));
    });
    // A Desk stop clicked on the timeline arrives as `at`: jump there once, then drop it so the same stop can jump again.
    effect(() => {
      const at = this.at();
      if (at === undefined || this.status() !== 'ready') return;
      untracked(() => {
        this.jumpToIndex(this.items().findIndex((i) => chatEventId(i) >= at));
        this.routes.replace({ name: 'project', id: this.projectId(), tab: 'conversation' });
      });
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

  protected onDragEnter(e: DragEvent): void {
    if (!hasFiles(e)) return;
    e.preventDefault();
    this.drags++;
    this.dropping.set(true);
  }

  protected onDragLeave(e: DragEvent): void {
    if (!hasFiles(e)) return;
    this.drags = Math.max(0, this.drags - 1);
    if (!this.drags) this.dropping.set(false);
  }

  /** Files dropped anywhere on the chat go to the composer's attachments. */
  protected onDrop(e: DragEvent): void {
    if (!hasFiles(e)) return;
    e.preventDefault();
    this.drags = 0;
    this.dropping.set(false);
    void this.composer()?.attach(e.dataTransfer?.files ?? null);
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
