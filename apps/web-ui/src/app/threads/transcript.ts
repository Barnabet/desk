import { NgTemplateOutlet } from '@angular/common';
import {
  afterNextRender,
  afterRenderEffect,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  input,
  model,
  output,
  signal,
  viewChild,
  ViewEncapsulation,
} from '@angular/core';
import { agentTitle, messageById, type MessagesState, type ToolCallView, type TranscriptEntry } from '@desk/client';
import { clip } from '@desk/protocol';
import { cardTitle, clock, href, inCard, isCard, outCard, policyReason, stopText, type CardView, type NarrativeRow, type Stop, type StopText } from '@desk/ui-core';
import { Button } from '../components/button';
import { CodeBlock } from '../components/code-block';
import { ConfirmDialog } from '../components/confirm-dialog';
import { ImageThumbs } from '../components/image-thumbs';
import { SafeMarkdown } from '../components/safe-markdown';
import { TemplateOf, templateOf } from '../components/template-of';
import { ToastService } from '../components/toast';
import { ToolGroup, ToolStatus } from '../components/tool-group';
import { DeskBridge } from '../core/desk-bridge';

export type Depth = 'narrative' | 'steps';

/**
 * The box under the transcript (design spec §8 item 5): `steer` a working or stopped thread; `ask` a done, failed or
 * idle one (the user's Ask), whose secondary action reopens (`Reopen`) or resumes (`Resume`, idle) it after a confirm;
 * `off` for an archived thread.
 */
export type ComposerMode = { kind: 'steer'; hint: string } | { kind: 'ask'; reopen: 'Reopen' | 'Resume' } | { kind: 'off'; hint: string };

/** The DOM id of a stop's transcript entry: the route and `?at=` scroll to it. */
export const stopDomId = (n: number) => `tr-stop-${n}`;

const MAX_OUTPUT = 6000;

type Incoming = Extract<TranscriptEntry, { kind: 'incoming' }>;
type Approval = Extract<TranscriptEntry, { kind: 'approval' }>;
type Pending = { text: string; ask: boolean };
/** A Narrative row: a stop with its words, or the compaction divider (`stop` null). */
type NarrativeView = { id: string; stop: Stop | null; text: StopText; when: string };

function pretty(args: string): string {
  try {
    return JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    return args;
  }
}

/** One tool call at full detail (Every step): its arguments, its output (long output behind "Show all"), its images. */
@Component({
  selector: 'div[deskToolCallFull]',
  imports: [CodeBlock, ImageThumbs, ToolStatus],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'tr-call' },
  template: `
    <div class="tr-call-head"><span class="mono grow">{{ c().name }}</span><span deskToolStatus [status]="c().status"></span></div>
    <div deskCodeBlock [code]="args()" language="arguments"></div>
    @if (out()) {
      <div deskCodeBlock [code]="shown()" [language]="c().status === 'ok' ? 'result' : c().status"></div>
      @if (cut() && !all()) {
        <button type="button" class="link small" (click)="all.set(true)">Show all {{ count() }} characters</button>
      }
    }
    @if (images().length) {
      <div deskImageThumbs [images]="images()"></div>
    }
  `,
})
export class ToolCallFull {
  readonly c = input.required<ToolCallView>();
  protected readonly all = signal(false);
  protected readonly args = computed(() => pretty(this.c().arguments));
  protected readonly out = computed(() => this.c().content ?? '');
  protected readonly cut = computed(() => this.out().length > MAX_OUTPUT);
  protected readonly shown = computed(() => (this.all() || !this.cut() ? this.out() : `${this.out().slice(0, MAX_OUTPUT)}\n…`));
  protected readonly count = computed(() => this.out().length.toLocaleString());
  protected readonly images = computed(() => this.c().images ?? []);
}

/** A message between this thread and another agent, as a compact card; the counterpart's name opens the pair sheet. */
@Component({
  selector: 'div[deskMessageCard]',
  imports: [SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'tr-card', '[class]': "'tr-card-' + c().dir", '[class.muted]': 'c().auto' },
  template: `
    <span class="tr-card-head">{{ words().before }}<button type="button" class="link tr-card-who" (click)="who($event)">{{ c().name }}</button>{{ words().after }} · {{ time() }}</span>
    <div deskSafeMarkdown [className]="'tr-card-text'" [text]="c().text"></div>
  `,
})
export class MessageCard {
  readonly c = input.required<CardView>();
  /** The counterpart's agent id (React's `onPair`). */
  readonly pair = output<string>();
  protected readonly words = computed(() => cardTitle(this.c()));
  protected readonly time = computed(() => clock(this.c().ts));

  protected who(ev: MouseEvent): void {
    // The card sits inside a stop's entry, whose click selects the stop.
    ev.stopPropagation();
    this.pair.emit(this.c().other);
  }
}

/** The transcript aside: Narrative (numbered stops) or Every step, plus the box that steers, asks or reopens the thread. */
@Component({
  selector: 'aside[deskTranscript]',
  imports: [NgTemplateOutlet, TemplateOf, Button, ConfirmDialog, SafeMarkdown, ToolGroup, ToolCallFull, MessageCard],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card transcript', 'aria-label': 'Transcript' },
  template: `
    <!-- EntrySummary: the body both depths show for an entry that is its own stop. -->
    <ng-template #summary let-e [deskTemplateOf]="entryType">
      @switch (e.kind) {
        @case ('brief') {
          <div deskSafeMarkdown [text]="e.text"></div>
        }
        @case ('detour') {
          <p class="tr-text">Continued on <span class="mono">{{ e.to }}</span> for the rest of the run. Nothing was lost.</p>
        }
        @case ('result') {
          <div deskSafeMarkdown [text]="e.summary"></div>
          @if (e.artifacts.length) {
            <div class="report-results">
              @for (a of e.artifacts; track a) {
                <a class="file-chip" [href]="libraryHref(a)">{{ a }}</a>
              }
            </div>
          }
        }
        @case ('revision') {
          <div deskSafeMarkdown [text]="e.feedback"></div>
        }
        @case ('steer') {
          <p class="tr-text">{{ e.text }}</p>
        }
        @case ('incoming') {
          <div deskSafeMarkdown [text]="e.text"></div>
        }
        @case ('approval') {
          <div class="tr-approval">
            <span class="mono small">{{ e.tool }} {{ clip(e.arguments, 120) }}</span>
            <span class="small">{{ reasonText(e.reason) }}</span>
            @if (e.state === 'pending') {
              <a [href]="approvalHref(e.approvalId)">Review in Attention</a>
            } @else {
              <span class="small muted">{{ outcome(e) }}</span>
            }
          </div>
        }
      }
    </ng-template>

    <!-- RunBody: a run's text and tool groups, with its message cards in place of the sends' tool rows (design spec §8 item 9). -->
    <ng-template #run let-entries [deskTemplateOf]="entriesType">
      @for (e of entries; track e.id) {
        @if (e.kind === 'assistant') {
          @if (e.text) {
            <div deskSafeMarkdown [className]="'md-voice tr-voice'" [text]="e.text"></div>
          }
        } @else if (isCard(e)) {
          <div deskMessageCard [c]="cardIn()(e)" (pair)="pair.emit($event)"></div>
        } @else if (e.kind === 'tools') {
          @let calls = unsent()(e.calls);
          @if (calls.length) {
            <div deskToolGroup [calls]="calls" [defaultOpen]="anyRunning(calls)" [titleOf]="titleOf()"></div>
          }
          @for (card of cardsOut()(e.calls); track card.id) {
            <div deskMessageCard [c]="card" (pair)="pair.emit($event)"></div>
          }
        }
      }
    </ng-template>

    <!-- AnswerBody: the question an answer run answers, its run, and the runtime's closure when it could not answer (design spec §8 item 6). -->
    <ng-template #answer let-s [deskTemplateOf]="stopType">
      @let a = answerOf()(s);
      @if (a.asked) {
        <p class="tr-asked">{{ a.asked }}</p>
      }
      <ng-container *ngTemplateOutlet="run; context: { $implicit: s.entries }" />
      @if (a.closure) {
        <p class="tr-text muted">{{ a.closure }}</p>
      }
    </ng-template>

    <ng-template #stopBody let-s [deskTemplateOf]="stopType">
      @switch (s.kind) {
        @case ('answer') {
          <ng-container *ngTemplateOutlet="answer; context: { $implicit: s }" />
        }
        @case ('work') {
          <ng-container *ngTemplateOutlet="run; context: { $implicit: s.entries }" />
        }
        @default {
          <ng-container *ngTemplateOutlet="summary; context: { $implicit: s.entries[0] }" />
        }
      }
    </ng-template>

    <!-- EntryFull: one entry at full detail (Every step). -->
    <ng-template #full let-e [deskTemplateOf]="entryType">
      @switch (e.kind) {
        @case ('brief') {
          <span class="eyebrow">Brief from Desk · {{ clock(e.ts) }}</span>
          <div deskSafeMarkdown [text]="e.text"></div>
        }
        @case ('status') {
          <span class="muted small">Status → {{ e.status }}{{ e.reason ? ' · ' + e.reason : '' }} · {{ clock(e.ts) }}</span>
        }
        @case ('assistant') {
          <div deskSafeMarkdown [className]="'md-voice tr-voice'" [text]="e.text"></div>
          @if (e.interrupted) {
            <span class="muted small">Cut off by an error.</span>
          }
        }
        @case ('tools') {
          <div class="tr-calls">
            @for (c of unsent()(e.calls); track c.id) {
              <div deskToolCallFull [c]="c"></div>
            }
            @for (card of cardsOut()(e.calls); track card.id) {
              <div deskMessageCard [c]="card" (pair)="pair.emit($event)"></div>
            }
          </div>
        }
        @case ('compacted') {}
        @case ('answer') {
          <span class="muted small">Woke to answer message #{{ e.question }} · {{ clock(e.ts) }}</span>
        }
        @case ('incoming') {
          @if (isCard(e)) {
            <div deskMessageCard [c]="cardIn()(e)" (pair)="pair.emit($event)"></div>
          } @else {
            <ng-container *ngTemplateOutlet="summary; context: { $implicit: e }" />
          }
        }
        @default {
          <ng-container *ngTemplateOutlet="summary; context: { $implicit: e }" />
        }
      }
    </ng-template>

    <div class="transcript-head">
      <h2>Transcript</h2>
      <div class="segmented" role="group" aria-label="Depth">
        <button type="button" [attr.aria-pressed]="depth() === 'narrative'" (click)="depth.set('narrative')">Narrative</button>
        <button type="button" [attr.aria-pressed]="depth() === 'steps'" (click)="depth.set('steps')">Every step</button>
      </div>
      <ng-content />
    </div>
    <div class="transcript-list" #list>
      @if (depth() === 'narrative') {
        @for (r of narrative(); track r.id) {
          @if (r.stop; as stop) {
            <div [id]="stopDomId(stop.n)" class="tr-entry" [class.selected]="selected() === stop.n" (click)="selectStop.emit(stop.n)">
              <span class="tr-num" [attr.aria-label]="'Stop ' + stop.n">{{ stop.n }}</span>
              <div class="tr-body">
                <span class="tr-title" [class.muted]="r.text.muted">@if (stop.kind === 'answer' && stop.live) {<span class="live-dot" aria-hidden="true"></span>}{{ r.text.title }}{{ r.when }}@if (stop.kind === 'work') {<span class="muted"> · {{ r.text.sub }}</span>}</span>
                <ng-container *ngTemplateOutlet="stopBody; context: { $implicit: stop }" />
              </div>
            </div>
          } @else {
            <div class="chat-divider" role="separator">Earlier conversation summarised</div>
          }
        }
      } @else {
        @for (e of entries(); track e.id) {
          @if (e.kind === 'compacted') {
            <div class="chat-divider" role="separator">Earlier conversation summarised</div>
          } @else {
            @let n = stopOfEntry().get(e.id);
            @let inN = inStop().get(e.id);
            <div [attr.id]="n === undefined ? null : stopDomId(n)" class="tr-entry" [class.selected]="inN !== undefined && inN === selected()" (click)="pick(inN)">
              @if (n === undefined) {
                <span class="tr-num tr-num-blank" aria-hidden="true"></span>
              } @else {
                <span class="tr-num" [attr.aria-label]="'Stop ' + n">{{ n }}</span>
              }
              <div class="tr-body">
                <ng-container *ngTemplateOutlet="full; context: { $implicit: e }" />
              </div>
            </div>
          }
        }
      }
      @for (p of pending(); track p.text) {
        <div class="tr-entry pending">
          <span class="tr-num tr-num-blank" aria-hidden="true"></span>
          <div class="tr-body">
            <span class="tr-title">You · {{ p.ask ? 'asking' : 'steering' }}…</span>
            <p class="tr-text">{{ p.text }}</p>
          </div>
        </div>
      }
    </div>
    <div class="steer">
      <label [attr.for]="'steer-' + threadId()">{{ ask() ? 'Ask this thread' : 'Steer this thread' }}</label>
      <textarea
        [id]="'steer-' + threadId()"
        rows="2"
        [value]="steer()"
        [disabled]="composer().kind === 'off'"
        [placeholder]="ask() ? 'Ask about its work. It answers from its context.' : 'Steer this thread. It reads this at its next step.'"
        (input)="onInput($event)"
        (keydown)="onKey($event)"
      ></textarea>
      <div class="steer-bar">
        <span class="grow muted small">{{ hint() }}</span>
        @if (reopenLabel(); as reopen) {
          <button deskButton size="sm" [pending]="sending() === 'steer'" [disabled]="!steer().trim() || sending() !== null" (click)="reopening.set(true)">{{ reopen }} with this…</button>
        }
        <button
          deskButton
          variant="primary"
          size="sm"
          [pending]="sending() === (ask() ? 'ask' : 'steer')"
          [disabled]="composer().kind === 'off' || !steer().trim() || sending() !== null"
          (click)="send(ask() ? 'ask' : 'steer')"
        >{{ ask() ? 'Ask' : 'Steer' }}</button>
      </div>
    </div>
    @if (reopening()) {
      @if (reopenLabel(); as reopen) {
        <div deskConfirmDialog [title]="reopen + ' this thread?'" [confirmLabel]="reopen" (confirm)="confirmReopen()" (cancel)="reopening.set(false)">{{ reopen === 'Reopen' ? 'Reopening' : 'Resuming' }} lets it change its work; its result and branch may change.</div>
      }
    }
  `,
})
export class Transcript {
  readonly projectId = input.required<string>();
  readonly threadId = input.required<string>();
  readonly rows = input.required<NarrativeRow[]>();
  readonly entries = input.required<TranscriptEntry[]>();
  readonly reviewRounds = input.required<number>();
  /** The session's message fold: names senders and titles answer runs. */
  readonly messages = input.required<MessagesState>();
  /** The messages this thread sent, by the tool call that sent them (sentCalls): those calls show as cards. */
  readonly sent = input.required<ReadonlyMap<string, number>>();
  readonly selected = input<number | null>(null);
  /** Narrative or Every step (React's `depth` and `onDepth`). */
  readonly depth = model.required<Depth>();
  /** The box under the transcript: steer a working thread, ask (or reopen) a finished one, or nothing. */
  readonly composer = input.required<ComposerMode>();
  /** Opens the pair sheet with another agent: a card's counterpart. */
  readonly pair = output<string>();
  /** React's `onSelect`: the stop whose entry was clicked. */
  readonly selectStop = output<number>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly list = viewChild.required<ElementRef<HTMLElement>>('list');
  /** Whether the list follows new entries: until the user scrolls up. */
  private pinned = true;
  protected readonly steer = signal('');
  protected readonly sending = signal<'ask' | 'steer' | null>(null);
  protected readonly pending = signal<Pending[]>([]);
  protected readonly reopening = signal(false);
  protected readonly ask = computed(() => this.composer().kind === 'ask');
  protected readonly reopenLabel = computed(() => {
    const c = this.composer();
    return c.kind === 'ask' ? c.reopen : null;
  });
  protected readonly hint = computed(() => {
    const c = this.composer();
    return c.kind === 'ask' ? 'It answers from its context; its result stays as it is.' : c.hint;
  });

  protected readonly clock = clock;
  protected readonly clip = clip;
  protected readonly isCard = isCard;
  protected readonly stopDomId = stopDomId;
  protected readonly entryType = templateOf<TranscriptEntry>();
  protected readonly entriesType = templateOf<TranscriptEntry[]>();
  protected readonly stopType = templateOf<Stop>();
  /** Names a thread by its id in tool rows (design spec §8 item 12). */
  protected readonly titleOf = computed(() => {
    const agents = this.messages().agents;
    // Own keys only: the id is the agent's thread_id argument, and 'constructor' must not find Object.prototype's.
    return (id: string): string | undefined => (Object.hasOwn(agents, id) ? (agents[id]?.title ?? undefined) : undefined);
  });

  protected readonly narrative = computed<NarrativeView[]>(() => {
    const rounds = this.reviewRounds();
    const m = this.messages();
    return this.rows().map((r) =>
      r.kind === 'compacted'
        ? { id: r.id, stop: null, text: { title: '', sub: '' }, when: '' }
        : {
            id: `stop:${r.stop.n}`,
            stop: r.stop,
            text: stopText(r.stop, rounds, m),
            when: r.stop.kind === 'work' || r.stop.kind === 'brief' || r.stop.kind === 'steer' ? '' : ` · ${clock(r.stop.from)}`,
          },
    );
  });
  /** The stop each entry begins (its number badge in Every step). */
  protected readonly stopOfEntry = computed(() => {
    const map = new Map<string, number>();
    for (const r of this.rows()) {
      const first = r.kind === 'stop' ? r.stop.entries[0] : undefined;
      if (r.kind === 'stop' && first) map.set(first.id, r.stop.n);
    }
    return map;
  });
  /** The stop each entry belongs to (what clicking it selects). */
  protected readonly inStop = computed(() => {
    const map = new Map<string, number>();
    for (const r of this.rows()) if (r.kind === 'stop') for (const e of r.stop.entries) map.set(e.id, r.stop.n);
    return map;
  });
  /** A tools entry's calls that sent no message; the same array for the same calls, so ToolGroup keeps its input. */
  protected readonly unsent = computed(() => {
    const sent = this.sent();
    const memo = new WeakMap<ToolCallView[], ToolCallView[]>();
    return (calls: ToolCallView[]): ToolCallView[] => {
      let hit = memo.get(calls);
      if (!hit) {
        hit = calls.filter((c) => !sent.has(c.id));
        memo.set(calls, hit);
      }
      return hit;
    };
  });
  /** The cards of the calls that sent a message, in call order. */
  protected readonly cardsOut = computed(() => {
    const sent = this.sent();
    const m = this.messages();
    const memo = new WeakMap<ToolCallView[], CardView[]>();
    return (calls: ToolCallView[]): CardView[] => {
      let hit = memo.get(calls);
      if (!hit) {
        hit = calls.flatMap((c) => {
          const id = sent.get(c.id);
          const msg = id === undefined ? undefined : messageById(m, id);
          return msg ? [outCard(msg, m)] : [];
        });
        memo.set(calls, hit);
      }
      return hit;
    };
  });
  /** The card of a message another thread sent this one. */
  protected readonly cardIn = computed(() => {
    const m = this.messages();
    const memo = new WeakMap<Incoming, CardView>();
    return (e: Incoming): CardView => {
      let hit = memo.get(e);
      if (!hit) {
        hit = inCard(e, m);
        memo.set(e, hit);
      }
      return hit;
    };
  });
  /** An answer run's question ("Frontend asked: “…”") and, when it could not answer another agent, the runtime's closure. */
  protected readonly answerOf = computed(() => {
    const m = this.messages();
    return (s: Stop): { asked: string | null; closure: string | null } => {
      const e = s.entries[0];
      const q = e?.kind === 'answer' ? messageById(m, e.question) : undefined;
      const reply = q?.answerId === undefined ? undefined : messageById(m, q.answerId);
      return {
        asked: q ? `${q.from === 'user' ? 'You' : agentTitle(m, q.from)} asked: “${clip(q.text, 200)}”` : null,
        closure: reply?.auto ? reply.text : null,
      };
    };
  });

  constructor() {
    // A send stays "You · steering…" until its steer entry lands.
    effect(() => {
      const entries = this.entries();
      const pending = this.pending();
      if (!pending.length) return;
      const steered = new Set(entries.flatMap((e) => (e.kind === 'steer' ? [e.text] : [])));
      const left = pending.filter((t) => !steered.has(t.text));
      if (left.length !== pending.length) this.pending.set(left);
    });
    afterRenderEffect(() => {
      this.entries();
      this.depth();
      const el = this.list().nativeElement;
      if (this.pinned) el.scrollTop = el.scrollHeight;
    });
    afterRenderEffect(() => {
      const n = this.selected();
      if (n === null) return;
      document.getElementById(stopDomId(n))?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    });
    // Not a template (scroll) listener: that would schedule change detection on every scroll event, for nothing.
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      const el = this.list().nativeElement;
      const onScroll = () => {
        this.pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      };
      el.addEventListener('scroll', onScroll, { passive: true });
      destroyRef.onDestroy(() => el.removeEventListener('scroll', onScroll));
    });
  }

  protected pick(n: number | undefined): void {
    if (n !== undefined) this.selectStop.emit(n);
  }

  protected anyRunning(calls: ToolCallView[]): boolean {
    return calls.some((c) => c.status === 'running');
  }

  protected libraryHref(file: string): string {
    return href({ name: 'project', id: this.projectId(), tab: 'library', file });
  }

  protected approvalHref(approvalId: string): string {
    return href({ name: 'attention', item: `approval:${approvalId}` });
  }

  protected reasonText(reason: string): string {
    return policyReason(reason).text;
  }

  protected outcome(e: Approval): string {
    return `${e.state === 'approved' ? 'Approved' : 'Denied'}${e.resolvedBy ? ` by ${e.resolvedBy === 'user' ? 'you' : e.resolvedBy}` : ''}${e.note ? ` · “${e.note}”` : ''}`;
  }

  protected onInput(e: Event): void {
    this.steer.set((e.target as HTMLTextAreaElement).value);
  }

  protected onKey(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      void this.send(this.ask() ? 'ask' : 'steer');
    }
  }

  protected confirmReopen(): void {
    this.reopening.set(false);
    void this.send('steer');
  }

  /** Sends the box's text: an Ask (`question`), or a plain message that steers the thread or reopens a finished one. */
  protected async send(how: 'ask' | 'steer'): Promise<void> {
    const text = this.steer().trim();
    if (!text || this.sending() || this.composer().kind === 'off') return;
    this.sending.set(how);
    try {
      await this.bridge.call('threads.send', { id: this.threadId(), text, ...(how === 'ask' ? { question: true } : {}) });
      this.steer.set('');
      this.pending.update((p) => [...p, { text, ask: how === 'ask' }]);
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.sending.set(null);
    }
  }
}
