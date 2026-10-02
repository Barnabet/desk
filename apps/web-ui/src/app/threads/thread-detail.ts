import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, effect, inject, input, signal, untracked } from '@angular/core';
import type { ThreadView } from '@desk/client';
import { acceptanceChip, answeringLabel, clock, duration, href, narrate, reviewVersion, sentCalls, stopAt, stopsOf, waitHop, waitLabel } from '@desk/ui-core';
import { AnsweringBadge } from '../components/answering-badge';
import { Button } from '../components/button';
import { ConfirmDialog } from '../components/confirm-dialog';
import { EmptyState } from '../components/empty-state';
import { HopLink } from '../components/hop-link';
import { PairSheet } from '../components/pair-sheet';
import { SkillBadge } from '../components/skill-badge';
import { statusLabel } from '../components/status-chip';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { NowService } from '../core/now.service';
import { SessionService, type SessionState } from '../core/session.service';
import { RouteView } from './route-view';
import { DiffTab } from './tabs/diff-tab';
import { FilesTab } from './tabs/files-tab';
import { ResultTab } from './tabs/result-tab';
import { ReviewTab } from './tabs/review-tab';
import { SkillDraftsTab } from './tabs/skill-drafts-tab';
import { tokens, usageByModel, UsageTab } from './tabs/usage-tab';
import { Transcript, type ComposerMode, type Depth } from './transcript';

type Tab = 'route' | 'result' | 'review' | 'diff' | 'files' | 'drafts' | 'usage';
const TABS: ReadonlyArray<readonly [Tab, string]> = [
  ['route', 'Route'],
  ['result', 'Result'],
  ['review', 'Review'],
  ['diff', 'Diff'],
  ['files', 'Files'],
  ['drafts', 'Skill drafts'],
  ['usage', 'Usage'],
];

const LIVE = new Set(['running', 'waiting', 'queued']);
const FINISHED = new Set(['done', 'failed', 'cancelled']);
/** Statuses whose box asks first: the thread answers from its context without reopening (design spec §4.8, §8 item 5). */
const ASKABLE = new Set(['done', 'failed', 'idle']);

/** Narrative or Every step, remembered in this browser (a convenience: storage may be unavailable). */
const DEPTH_KEY = 'desk.transcriptDepth';

function readDepth(): Depth {
  try {
    return localStorage.getItem(DEPTH_KEY) === 'steps' ? 'steps' : 'narrative';
  } catch {
    return 'narrative';
  }
}

/** One thread: its route, tabs and transcript. `at` (an event id, from `?at=`) selects the stop that holds it, once. */
@Component({
  selector: 'div[deskThreadDetail]',
  imports: [AnsweringBadge, Button, ConfirmDialog, EmptyState, HopLink, PairSheet, SkillBadge, RouteView, Transcript, ResultTab, ReviewTab, DiffTab, FilesTab, SkillDraftsTab, UsageTab],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'thread-detail' },
  template: `
    <div class="thread-main">
      <div class="thread-head">
        <a class="small" [href]="allThreads()">← All threads</a>
        <h1>{{ thread().title ?? 'Untitled thread' }}</h1>
        <p class="thread-status-line"><span [class]="'tone-' + label().tone">{{ statusText() }}</span>@if (hop(); as h) {&ngsp;<a deskHopLink [hop]="h"></a>}{{ reasonPart() }}{{ roundPart() }} · <span class="mono">{{ modelText() }}</span> · started {{ started() }} · {{ elapsed() }}{{ usagePart() }}</p>
        <div class="thread-chips">
          @if (answering(); as a) {
            <span deskAnsweringBadge [label]="a"></span>
          }
          @if (chip(); as c) {
            <span [class]="'chip chip-' + c.tone">{{ c.label }}</span>
          }
          @for (sk of thread().active_skills; track sk) {
            <span deskSkillBadge [name]="sk"></span>
          }
          <span class="chip chip-idle">@if (thread().git_branch; as branch) {<span class="mono">{{ branch }}</span>} @else {Scratch workspace}</span>
          @if (archived()) {
            <span class="chip chip-idle">Archived</span>
          }
          @if (thread().status === 'done') {
            <button deskButton size="sm" variant="ghost" [pending]="busy() === 'skill'" (click)="act('skill')">Turn into a skill</button>
          }
        </div>
      </div>
      <div class="tabs" role="tablist" aria-label="Thread">
        @for (t of tabs; track t[0]) {
          <button type="button" role="tab" [attr.aria-selected]="tab() === t[0]" (click)="tab.set(t[0])">{{ t[1] }}@if (t[0] === 'drafts' && drafts().length) {<span class="tab-count">{{ drafts().length }}</span>}</button>
        }
      </div>
      <div class="thread-tab" role="tabpanel">
        @switch (tab()) {
          @case ('route') {
            @if (stops().length) {
              <div
                deskRouteView
                [stops]="stops()"
                [running]="thread().status === 'running'"
                [activity]="thread().activity"
                [reviewRounds]="rounds()"
                [messages]="messages()"
                [selected]="current()"
                (selectStop)="selected.set($event)"
              ></div>
            } @else {
              <div deskEmptyState title="Not started yet" body="The route draws itself as the thread works."></div>
            }
          }
          @case ('result') {
            <div deskResultTab [projectId]="projectId()" [thread]="thread()"></div>
          }
          @case ('review') {
            <div deskReviewTab [thread]="thread()" [threads]="project().threads" [version]="reviewAt()" [now]="now()"></div>
          }
          @case ('diff') {
            <div deskDiffTab [threadId]="threadId()" [version]="version()"></div>
          }
          @case ('files') {
            <div deskFilesTab [threadId]="threadId()" [(dir)]="dir" [version]="version()"></div>
          }
          @case ('drafts') {
            <div deskSkillDraftsTab [projectId]="projectId()" [threadTitle]="thread().title ?? threadId()" [drafts]="drafts()" (browse)="browse($event)"></div>
          }
          @case ('usage') {
            <div deskUsageTab [usage]="usage()"></div>
          }
        }
      </div>
    </div>
    <aside
      deskTranscript
      [projectId]="projectId()"
      [threadId]="threadId()"
      [rows]="rows()"
      [entries]="entries()"
      [reviewRounds]="rounds()"
      [messages]="messages()"
      [sent]="sent()"
      [selected]="current()"
      [depth]="depth()"
      [composer]="composer()"
      (pair)="pairWith.set($event)"
      (selectStop)="selected.set($event)"
      (depthChange)="setDepth($event)"
    >
      @if (live()) {
        <button deskButton size="sm" [pending]="busy() === 'stop'" (click)="confirm.set('stop')">Stop</button>
      }
      @if (finished() && !archived()) {
        <button deskButton size="sm" [pending]="busy() === 'archive'" (click)="confirm.set('archive')">Archive</button>
      }
    </aside>
    @if (pairWith(); as other) {
      <div deskPairSheet [projectId]="projectId()" [messages]="messages()" [a]="threadId()" [b]="other" (close)="pairWith.set(null)"></div>
    }
    @if (confirm() === 'stop') {
      <div deskConfirmDialog title="Stop this thread?" confirmLabel="Stop thread" danger (confirm)="act('stop')" (cancel)="confirm.set(null)">It stops at once, and pending approvals are denied. Its workspace and any branch are kept, and Desk is told.</div>
    }
    @if (confirm() === 'archive') {
      <div deskConfirmDialog title="Archive this thread?" confirmLabel="Archive" (confirm)="act('archive')" (cancel)="confirm.set(null)">@if (thread().git_branch; as branch) {The workspace is removed. The branch <span class="mono">{{ branch }}</span> is kept, so you can still merge it.} @else {The scratch workspace is removed. Files it published stay in the Library.}</div>
    }
  `,
})
export class ThreadDetail {
  readonly s = input.required<SessionState>();
  readonly thread = input.required<ThreadView>();
  readonly at = input<number>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly sessions = inject(SessionService);
  private readonly global = inject(GlobalStore).state;
  protected readonly now = inject(NowService).now;
  protected readonly tabs = TABS;

  protected readonly project = computed(() => this.s().project!);
  protected readonly projectId = computed(() => this.project().project.id);
  protected readonly threadId = computed(() => this.thread().id);
  /** The session's fold and log, by identity: they change less often than the session state around them. */
  protected readonly messages = computed(() => this.s().messages);
  private readonly events = computed(() => this.s().events);
  protected readonly rounds = computed(() => this.project().project.settings.review_rounds);
  private readonly proxyDown = computed(() => this.global().system.proxy === 'down');
  private readonly attention = computed(() => this.global().attention);

  private readonly transcript = computed(() => this.sessions.transcript(this.projectId(), this.threadId())());
  protected readonly entries = computed(() => this.transcript().entries);
  /** The messages it sent replace their tool calls on the route (design spec §8 item 9). */
  protected readonly sent = computed(() => sentCalls(this.messages(), this.threadId()));
  protected readonly rows = computed(() => narrate(this.entries(), this.sent()));
  protected readonly stops = computed(() => stopsOf(this.rows()));

  protected readonly selected = signal<number | null>(null);
  protected readonly tab = signal<Tab>('route');
  protected readonly dir = signal('');
  protected readonly depth = signal<Depth>(readDepth());
  protected readonly confirm = signal<'stop' | 'archive' | null>(null);
  protected readonly busy = signal<'stop' | 'archive' | 'skill' | null>(null);
  /** The other agent of the open pair sheet (design spec §8 item 8): local state, no route. */
  protected readonly pairWith = signal<string | null>(null);
  /** The thread last shown, and the `at` already applied: its stop is selected once, so the user's own choice holds. */
  private seenThread: string | null = null;
  private openedAt: number | null = null;

  private readonly threadEvents = computed(() => this.events().filter((e) => e.agent_id === this.threadId()));
  protected readonly drafts = computed((): string[] => {
    const list = this.threadEvents();
    for (let i = list.length - 1; i >= 0; i--) {
      const e = list[i]!;
      if (e.type === 'agent.result') return e.payload.skill_drafts ?? [];
    }
    return [];
  });
  protected readonly usage = computed(() => usageByModel(this.threadEvents(), this.threadId()));
  /** Changes whenever the thread's events do: Diff and Files fetch again. */
  protected readonly version = computed(() => String(this.threadEvents().at(-1)?.id ?? 0));
  /** A reviewer's findings are its own events: the Review tab fetches again on any review event too. */
  protected readonly reviewAt = computed(() => reviewVersion(this.events(), this.threadId()));
  protected readonly chip = computed(() => acceptanceChip(this.thread()));

  protected readonly label = computed(() => statusLabel(this.thread().status, this.thread().reason, this.proxyDown()));
  protected readonly current = computed(() => this.selected() ?? this.stops().at(-1)?.n ?? null);
  protected readonly archived = computed(() => !!this.thread().archived_at);
  protected readonly live = computed(() => LIVE.has(this.thread().status));
  protected readonly finished = computed(() => FINISHED.has(this.thread().status));
  /** A cancelled thread keeps Steer: an Ask to it would only be an ordinary message (design spec §3.2, rule 4.1). */
  protected readonly composer = computed<ComposerMode>(() => {
    const t = this.thread();
    if (t.archived_at) return { kind: 'off', hint: 'This thread is archived.' };
    if (ASKABLE.has(t.status)) return { kind: 'ask', reopen: t.status === 'idle' ? 'Resume' : 'Reopen' };
    return { kind: 'steer', hint: t.status === 'cancelled' ? 'The thread wakes up to read this. For new work, message Desk.' : 'For new work, message Desk.' };
  });
  // What it waits on, and whether it is answering, come from the message fold, never the status reason (design spec §8).
  protected readonly wait = computed(() => (this.thread().status === 'waiting' ? waitLabel(this.messages(), this.threadId(), this.attention(), this.now()) : null));
  protected readonly answering = computed(() => answeringLabel(this.messages(), this.threadId()));
  protected readonly hop = computed(() => (this.wait() ? waitHop(this.messages(), this.threadId(), this.attention()) : null));

  protected readonly statusText = computed(() => {
    const w = this.wait();
    return w ? `${w[0]!.toUpperCase()}${w.slice(1)}` : this.label().label;
  });
  protected readonly reasonPart = computed(() => {
    const t = this.thread();
    return t.reason && t.status !== 'running' && t.status !== 'waiting' ? ` · ${t.reason}` : '';
  });
  protected readonly roundPart = computed(() => (this.thread().review_round ? ` · revision round ${this.thread().review_round} of ${this.rounds()}` : ''));
  protected readonly modelText = computed(() => {
    const t = this.thread();
    return `${t.model_override ?? t.model}${t.effort ? ` (${t.effort} effort)` : ''}`;
  });
  protected readonly started = computed(() => clock(this.thread().created_at));
  protected readonly elapsed = computed(() => duration(this.now() - Date.parse(this.thread().created_at)));
  protected readonly usagePart = computed(() => {
    const u = this.usage();
    return u.length ? ` · ${u.map((x) => `${x.model.replace(/^claude-/, '')} ${tokens(x.prompt + x.completion)}`).join(' · ')}` : '';
  });
  protected readonly allThreads = computed(() => href({ name: 'project', id: this.projectId(), tab: 'threads' }));

  constructor() {
    effect(() => {
      const id = this.threadId();
      const at = this.at();
      const stops = this.stops();
      const messages = this.messages();
      untracked(() => {
        // Another thread: its own stop, tab, folder and pair sheet (React's reset effect on thread.id).
        if (id !== this.seenThread) {
          this.seenThread = id;
          this.selected.set(null);
          this.tab.set('route');
          this.dir.set('');
          this.pairWith.set(null);
          this.openedAt = null;
        }
        if (at === undefined || this.openedAt === at) return;
        // The stop that holds the message (its entry, or the card of a send), or the one that shows what it came from.
        const stop = stopAt(stops, messages, id, at);
        if (!stop) return;
        this.openedAt = at;
        this.tab.set('route');
        this.selected.set(stop.n);
      });
    });
  }

  protected setDepth(d: Depth): void {
    this.depth.set(d);
    try {
      localStorage.setItem(DEPTH_KEY, d);
    } catch {
      // A convenience only.
    }
  }

  protected browse(dir: string): void {
    this.dir.set(dir);
    this.tab.set('files');
  }

  protected async act(what: 'stop' | 'archive' | 'skill'): Promise<void> {
    const t = this.thread();
    this.confirm.set(null);
    this.busy.set(what);
    try {
      if (what === 'stop') await this.bridge.call('threads.stop', { id: t.id });
      else if (what === 'archive') await this.bridge.call('threads.archive', { id: t.id });
      else {
        await this.bridge.call('projects.send', { id: this.projectId(), text: `Turn what the thread "${t.title ?? t.id}" did into a reusable skill.` });
        this.toasts.toast({ tone: 'info', message: 'Asked Desk to turn this thread into a skill.' });
      }
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(null);
    }
  }
}
