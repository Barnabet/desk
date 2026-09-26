import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, output, signal } from '@angular/core';
import type { ChatItem } from '@desk/client';
import { WAKES_PAUSED } from '@desk/protocol';
import { chatEventId, clock, duration, href, plural, type AnswerQuote, type PairLine, type RowView } from '@desk/ui-core';
import { Button } from '../components/button';
import { SafeMarkdown } from '../components/safe-markdown';
import { ToastService } from '../components/toast';
import { ToolGroup } from '../components/tool-group';
import { DeskBridge } from '../core/desk-bridge';

const FEED: Record<string, string> = {
  note: 'Note',
  update: 'Update',
  question: 'Question',
  blocker: 'Blocked',
  answer: 'Answer',
  completed: 'Result',
  failed: 'Failed',
  cancelled: 'Stopped',
  approval: 'Approval',
  stalled: 'Stalled',
  revision: 'Sent back',
};

/** The daemon labels threads as `thread "Title" (id)`; people only need the title. */
export const agentLabel = (label: string) => /^thread "(.*)" \([\w-]+\)$/.exec(label)?.[1] ?? label;

/** The element id of a chat row (the screen scrolls to it and flashes it). */
export const chatDomId = (id: string) => `chat-${id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;

/** Time since `iso` on the shared clock: "42s", "4m". */
const age = (iso: string, now: number) => duration(Math.max(0, now - Date.parse(iso)));

/** A runtime closure's text without its parentheses: "(Frontend was stopped before answering.)" → "Frontend was …". */
const unwrap = (text: string) => text.replace(/^\(([\s\S]*)\)$/, '$1');

const CANNOT = 'could not answer: ';

/**
 * Why `title` could not answer, from the runtime's closure `(<title> <why>.)`: "Desk was restarting. …" when the closure
 * already says it could not answer, "it was stopped before answering." otherwise, or the whole text if it is not one.
 */
function closureReason(title: string, text: string): string {
  const body = unwrap(text);
  if (!body.startsWith(`${title} `)) return body;
  const why = body.slice(title.length + 1);
  return why.startsWith(CANNOT) ? why.slice(CANNOT.length) : `it ${why}`;
}

/** Kinds a thread writes to Desk itself; the rest on Desk's stream are the runtime's notices about it. */
const WRITTEN: ReadonlySet<string> = new Set(['note', 'update', 'question', 'blocker', 'answer']);

/**
 * One row of the conversation. The host is the row wrapper (`div.chat-item#chat-<id>`), which the screen scrolls to and
 * flashes. `view` is what the row shows from the message fold (rowViews); `now` comes only to rows whose view ticks.
 * Agent text always goes through SafeMarkdown.
 */
@Component({
  selector: 'div[deskChatItem]',
  imports: [NgTemplateOutlet, Button, SafeMarkdown, ToolGroup],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'chat-item', '[id]': 'domId()' },
  template: `
    <!-- A tracked question's state line: amber "waiting for an answer · 4m", "answered 14:05 ↓" (jumps to the answer), or muted. -->
    <ng-template #questionState let-q>
      @switch (q.state) {
        @case ('open') {
          <span class="msg-state msg-open">waiting for an answer · {{ age(q.since) }}</span>
        }
        @case ('answered') {
          @if (q.answerId !== undefined && q.answeredAt) {
            <button type="button" class="link small msg-state" (click)="jump.emit('e:' + q.answerId)">answered {{ clock(q.answeredAt) }} ↓</button>
          } @else {
            <span class="msg-state muted">{{ q.state }}</span>
          }
        }
        @default {
          <span class="msg-state muted">{{ q.state }}</span>
        }
      }
    </ng-template>
    <!-- A message's text, at most three lines until "more". -->
    <ng-template #clamped let-text>
      <div deskSafeMarkdown [className]="long() && !clampOpen() ? 'feed-text clamp-3' : 'feed-text'" [text]="text"></div>
      @if (long()) {
        <button type="button" class="link small feed-more" (click)="clampOpen.set(!clampOpen())">{{ clampOpen() ? 'less' : 'more' }}</button>
      }
    </ng-template>
    @let it = item();
    @let v = view();
    @switch (it.kind) {
      @case ('user') {
        <div class="chat-user"><span class="chat-meta">You · {{ clock(it.ts) }}</span><p>{{ it.text }}</p></div>
      }
      @case ('assistant') {
        <div class="chat-desk" [attr.aria-live]="it.streaming ? 'polite' : null">
          <span class="chat-meta">
            @if (it.streaming) {
              <span class="live-dot" aria-hidden="true"></span><ng-container>Desk · writing</ng-container>
            } @else {
              <ng-container>Desk · {{ clock(it.ts) }}</ng-container>
            }
          </span>
          <div deskSafeMarkdown [className]="'md-voice'" [text]="it.text"></div>
          @if (it.interrupted) {
            <span class="muted small">Cut off by an error; Desk will pick up again.</span>
          }
        </div>
      }
      @case ('tools') {
        <!-- Desk's sends to threads show as message rows; failed, denied or unfinished ones stay here (design spec §8 item 1). -->
        @if (toolCalls().length) {
          <div deskToolGroup [calls]="toolCalls()" [title]="toolsTitle()" [titleOf]="titleOf()"></div>
        }
      }
      @case ('agent') {
        @if (it.auto) {
          <!-- The runtime closed Desk's question for a thread that could not answer: never shown as an answer. -->
          <div class="chat-feed feed-closed">
            <span class="feed-closed-text">{{ closedText() }}</span>
            <span class="muted small">{{ clock(it.ts) }}</span>
            @if (v?.answers; as a) {
              <button type="button" class="msg-quote" (click)="jump.emit('e:' + a.question)">{{ quote(a) }}</button>
            }
          </div>
        } @else {
          <div class="chat-feed" [class]="'feed-' + it.messageKind">
            <span class="feed-chip">{{ feed(it.messageKind) }}</span>
            @if (written(it.messageKind)) {
              <button type="button" class="link feed-from" (click)="openPair(it.fromAgentId, deskId())">{{ agentLabel(it.fromLabel) }}</button>
            } @else {
              <a class="feed-from" [href]="threadHref(it.fromAgentId)">{{ agentLabel(it.fromLabel) }}</a>
            }
            <span class="muted small">{{ clock(it.ts) }}</span>
            @if (v?.answers; as a) {
              <button type="button" class="msg-quote" (click)="jump.emit('e:' + a.question)">{{ quote(a) }}</button>
            }
            <div deskSafeMarkdown [className]="'feed-text'" [text]="it.text"></div>
            @if (v?.question; as q) {
              <ng-container *ngTemplateOutlet="questionState; context: { $implicit: q }" />
            }
          </div>
        }
      }
      @case ('message') {
        <div class="chat-msg" [class]="'msg-' + it.messageKind">
          <span class="msg-head">Desk → <button type="button" class="link msg-who" (click)="openPair(it.to, it.from)">{{ v?.toTitle ?? 'a thread' }}</button> · {{ it.messageKind }} · {{ clock(it.ts) }}</span>
          @if (v?.answers; as a) {
            <button type="button" class="msg-quote" (click)="jump.emit('e:' + a.question)">{{ quote(a) }}</button>
          }
          <ng-container *ngTemplateOutlet="clamped; context: { $implicit: it.text }" />
          @if (v?.question; as q) {
            <ng-container *ngTemplateOutlet="questionState; context: { $implicit: q }" />
          }
        </div>
      }
      @case ('steer') {
        <div class="chat-msg msg-steer">
          <span class="msg-head">{{ it.question ? 'You asked ' : 'You → ' }}<a [href]="threadHref(it.to)">{{ v?.toTitle ?? 'a thread' }}</a> · {{ clock(it.ts) }}</span>
          <ng-container *ngTemplateOutlet="clamped; context: { $implicit: it.text }" />
        </div>
      }
      @case ('digest') {
        <!-- What threads said to each other during one Desk turn: counts, then one line per pair (design spec §8 items 2 and 8). -->
        @if (v?.digest; as d) {
          <div class="chat-digest">
            <button type="button" class="digest-toggle" [attr.aria-expanded]="digestOpen()" (click)="digestOpen.set(!digestOpen())">{{ digestLabel(d) }}</button>
            @if (digestOpen()) {
              <ul class="digest-pairs">
                @for (p of d.pairs; track p.key) {
                  <li><button type="button" class="link digest-pair" (click)="openPair(p.a, p.b)">{{ pairLabel(p) }}</button></li>
                }
              </ul>
            }
          </div>
        }
      }
      @case ('report') {
        <article class="report-card" [attr.aria-labelledby]="'report-' + it.eventId">
          <div class="report-main">
            <span class="eyebrow">Report · {{ clock(it.ts) }}</span>
            <h2 [id]="'report-' + it.eventId">{{ it.headline }}</h2>
            @if (it.progress) {
              <div deskSafeMarkdown [className]="'report-progress'" [text]="it.progress"></div>
            }
            @if (it.results.length) {
              <div class="report-results">
                <strong>Results</strong>
                @for (r of it.results; track r) {
                  <a class="file-chip" [href]="libraryHref(r)">{{ r }}</a>
                }
              </div>
            }
          </div>
          @if (it.needsYou.length) {
            <div class="needs-box report-needs">
              <strong>Needs you · {{ it.needsYou.length }}</strong>
              @for (need of it.needsYou; track $index; let i = $index) {
                @let needId = 'report:' + it.eventId + ':' + i;
                <div class="needs-row">
                  <span class="needs-num" aria-hidden="true">{{ i + 1 }}</span>
                  @if (attentionIds().has(needId)) {
                    <a [href]="attentionHref(needId)">{{ need }}</a>
                  } @else {
                    <span class="needs-done">{{ need }}</span>
                  }
                </div>
              }
            </div>
          }
        </article>
      }
      @case ('question') {
        <section class="question-card" [class.answered]="it.answered" [attr.aria-labelledby]="'question-' + it.eventId">
          <span class="eyebrow"><span class="accent-dot" aria-hidden="true"></span>Desk asks · {{ clock(it.ts) }}</span>
          <p [id]="'question-' + it.eventId" class="question-text">{{ it.question }}</p>
          @if (it.answered) {
            <span class="muted small">Answered</span>
          } @else {
            <div class="choice-options">
              @for (opt of it.options; track opt; let i = $index) {
                <button deskButton size="sm" [variant]="i === 0 ? 'primary' : 'secondary'" [pending]="answering() === opt" [disabled]="answering() !== null" (click)="answer.emit(opt)">{{ opt }}</button>
              }
              <button type="button" class="link small" (click)="ownWords.emit()">Answer in your own words…</button>
            </div>
          }
        </section>
      }
      @case ('notice') {
        <!-- The pause's own item is paused:<notice id>: Resume shows while it is listed (design spec §8 item 7). -->
        <div class="chat-notice" [class]="'notice-' + it.level" role="status"><span class="dot" [class]="it.level === 'info' ? 'ok' : 'warn'" aria-hidden="true"></span>{{ it.code === 'proxy_down' ? 'Paused, will resume: the model proxy is unreachable.' : it.message }}@if (resumeId(); as id) {<button deskButton size="sm" class="chat-notice-action" [pending]="resumeBusy()" (click)="resume(id)">Resume</button>}</div>
      }
      @case ('compacted') {
        <div class="chat-divider" role="separator">Earlier conversation summarised</div>
      }
    }
  `,
})
export class ChatItemView {
  readonly item = input.required<ChatItem>();
  readonly projectId = input.required<string>();
  readonly attentionIds = input.required<ReadonlySet<string>>();
  readonly answering = input<string | null>(null);
  /** What the row shows from the message fold (rowViews): a question's state, an answer's quote, a digest's counts. */
  readonly view = input<RowView | undefined>();
  /** The shared clock, passed only to rows whose view ticks; the others never re-render for it. */
  readonly now = input<number | undefined>();
  /** Desk's agent id: the other side of a message row's pair. */
  readonly deskId = input.required<string>();
  /** A question card's option was chosen. */
  readonly answer = output<string>();
  /** "Answer in your own words…": the screen focuses the composer. */
  readonly ownWords = output<void>();
  /** Scroll the chat to an item and flash it. */
  readonly jump = output<string>();
  /** Open the pair sheet of two agents (design spec §8 item 8). */
  readonly pair = output<readonly [string, string]>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly clampOpen = signal(false);
  protected readonly digestOpen = signal(false);
  /** Resume stays busy after success: the row loses the button once the attention list drops the item (design spec §5.4). */
  protected readonly resumeBusy = signal(false);
  protected readonly clock = clock;
  protected readonly agentLabel = agentLabel;
  protected readonly domId = computed(() => chatDomId(this.item().id));
  protected readonly toolCalls = computed(() => {
    const it = this.item();
    return it.kind === 'tools' ? it.calls.filter((c) => !(c.name === 'message_thread' && c.status === 'ok')) : [];
  });
  protected readonly toolsTitle = computed(() => {
    const calls = this.toolCalls();
    return `Desk ${calls.some((c) => c.status === 'running') ? 'is using' : 'used'} ${calls.length} tool${calls.length === 1 ? '' : 's'}`;
  });
  /** ToolGroup's titleOf: one function per view, so the group re-renders only when the titles change. */
  protected readonly titleOf = computed(() => {
    const titles = this.view()?.titles;
    return (id: string): string | undefined => titles?.[id];
  });
  protected readonly long = computed(() => {
    const it = this.item();
    if (it.kind !== 'message' && it.kind !== 'steer') return false;
    return it.text.length > 280 || it.text.split('\n').length > 3;
  });
  protected readonly closedText = computed(() => {
    const it = this.item();
    if (it.kind !== 'agent') return '';
    const who = agentLabel(it.fromLabel);
    return `${who} could not answer: ${closureReason(who, it.text)}`;
  });
  /** The paused notice's attention item while it is listed, else null. */
  protected readonly resumeId = computed(() => {
    const it = this.item();
    if (it.kind !== 'notice' || it.code !== WAKES_PAUSED) return null;
    const id = `paused:${chatEventId(it)}`;
    return this.attentionIds().has(id) ? id : null;
  });

  protected age(iso: string): string {
    return age(iso, this.now() ?? Date.now());
  }

  protected feed(kind: string): string {
    return FEED[kind] ?? kind;
  }

  protected written(kind: string): boolean {
    return WRITTEN.has(kind);
  }

  /** An answer's one-line quote of its question; it jumps to the question and flashes it. */
  protected quote(a: AnswerQuote): string {
    return `↩ ${a.asker}'s question: “${a.text}”`;
  }

  protected digestLabel(d: NonNullable<RowView['digest']>): string {
    return `Between threads · ${plural(d.messages, 'message')}${d.open ? ` · ${plural(d.open, 'open question')}` : ''}`;
  }

  protected pairLabel(p: PairLine): string {
    return `${p.label} · ${p.count}${p.waiting ? ` · ${p.waiting.who} waiting ${this.age(p.waiting.since)}` : ''}`;
  }

  protected openPair(a: string, b: string): void {
    this.pair.emit([a, b]);
  }

  protected threadHref(threadId: string): string {
    return href({ name: 'project', id: this.projectId(), tab: 'threads', threadId });
  }

  protected libraryHref(file: string): string {
    return href({ name: 'project', id: this.projectId(), tab: 'library', file });
  }

  protected attentionHref(item: string): string {
    return href({ name: 'attention', item });
  }

  /** Resumes a paused project from its notice (design spec §5.4): dismissing its `paused` item resumes the agents. */
  protected async resume(id: string): Promise<void> {
    this.resumeBusy.set(true);
    try {
      await this.bridge.call('attention.dismiss', { id });
    } catch (err) {
      this.toasts.error(err);
      this.resumeBusy.set(false);
    }
  }
}
