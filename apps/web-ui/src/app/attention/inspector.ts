import { ChangeDetectionStrategy, Component, computed, inject, input, output, signal, ViewEncapsulation } from '@angular/core';
import type { AttentionItem } from '@desk/protocol';
import { clock, href, KIND_NAME, policyReason, STRIP_CODE, waited } from '@desk/ui-core';
import { Button } from '../components/button';
import { CodeBlock } from '../components/code-block';
import { SafeMarkdown } from '../components/safe-markdown';
import { injectSession, SessionService } from '../core/session.service';

const SHELL = new Set(['bash', 'bash_background', 'bash_readonly']);

/** A shell command reads as `$ command`; anything else as pretty JSON. */
export function describeArgs(tool: string, args: string): { command: string | null; pretty: string } {
  try {
    const v = JSON.parse(args) as Record<string, unknown>;
    const pretty = JSON.stringify(v, null, 2);
    const command = v['command'];
    if (SHELL.has(tool) && typeof command === 'string') return { command, pretty };
    const script = v['script'];
    if (tool === 'skill_run' && typeof script === 'string') {
      const rest = v['args'];
      return { command: [v['skill'], script, ...(Array.isArray(rest) ? rest : [])].filter(Boolean).join(' '), pretty };
    }
    return { command: null, pretty };
  } catch {
    return { command: null, pretty: args };
  }
}

/**
 * The selected strip in full, with the controls to act on it. The screen owns the note, the pending decision and which
 * questions were answered (React's InspectorActions); this component reports what the person does through its outputs.
 */
@Component({
  selector: 'article[deskInspector]',
  imports: [Button, CodeBlock, SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card inspector', '[attr.aria-label]': "'Selected: ' + kindName().toLowerCase()" },
  template: `
    <div class="inspector-eyebrow">
      <span class="strip-code-badge" [class]="'code-' + item().kind">{{ code() }}</span>
      <span class="eyebrow">{{ kindName() }}</span>
      <span class="muted">· {{ item().project_name }} · {{ when() }}</span>
      <span class="grow"></span>
      <span class="muted">{{ index() + 1 }} of {{ total() }}</span>
    </div>
    @switch (item().kind) {
      @case ('approval') {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (args(); as a) {
          @if (a.command && !raw()) {
            <pre class="command" aria-label="Command"><span class="command-prompt">$ </span>{{ a.command }}</pre>
          } @else {
            <div deskCodeBlock [code]="raw() ? rawArguments() : a.pretty" [language]="raw() ? 'raw arguments' : 'arguments'"></div>
          }
          <button type="button" class="link small inspector-raw" (click)="raw.set(!raw())">{{ raw() ? 'Show readable' : 'Show raw arguments' }}</button>
        } @else {
          <p class="muted">{{ s().status === 'loading' ? 'Loading the request…' : 'The request details are not available.' }}</p>
        }
        <div class="facts">
          <div class="fact"><span class="fact-label">TOOL</span><span class="mono fact-value">{{ approval()?.tool ?? '—' }}</span></div>
          <div class="fact"><span class="fact-label">{{ agent()?.role === 'desk' ? 'ASKED BY' : 'THREAD' }}</span><span class="fact-value">@if (threadHref(); as h) {<a [href]="h">{{ agent()?.title ?? 'Thread' }}</a>} @else {<ng-container>Desk</ng-container>}</span></div>
          <div class="fact"><span class="fact-label">BRANCH</span><span class="mono fact-value">{{ agent()?.git_branch ?? 'scratch workspace' }}</span></div>
          <div class="fact"><span class="fact-label">PROJECT</span><span class="fact-value"><a [href]="conversationHref()">{{ item().project_name }}</a></span></div>
          <div class="fact"><span class="fact-label">WORKDIR</span><span class="mono fact-value"><span [attr.title]="agent()?.workspace_path ?? ''">{{ workdir() }}</span></span></div>
          <div class="fact"><span class="fact-label">REQUESTED</span><span class="fact-value">{{ requested() }}</span></div>
        </div>
        <div class="why">
          <div class="why-row">
            <span class="why-label">Why it's asking</span>
            <div class="why-body">
              <p>{{ why().text }}</p>
              @if (why().chip; as chip) {<span class="rule-chip mono">{{ chip }}</span>}
            </div>
          </div>
          <div class="why-row">
            <span class="why-label">What the thread said</span>
            @if (lastWords(); as words) {<div deskSafeMarkdown [className]="'why-quote'" [text]="words"></div>} @else {<p class="muted">Nothing yet.</p>}
          </div>
        </div>
        <div class="field">
          <label for="attention-note">Note to the thread (optional)</label>
          <input id="attention-note" type="text" [value]="note()" (input)="setNote($event)" placeholder="e.g. Use npm test instead" maxlength="2000" />
        </div>
        <div class="inspector-actions">
          <button deskButton variant="primary" [pending]="busy() === 'approved'" [disabled]="busy() !== null" (click)="resolve.emit('approved')">Approve once <kbd>⌘⏎</kbd></button>
          <button deskButton [pending]="busy() === 'denied'" [disabled]="busy() !== null" (click)="resolve.emit('denied')">Deny <kbd>⌘⌫</kbd></button>
          <span class="grow"></span>
          <a class="small" [href]="settingsHref()">Edit policy rules</a>
        </div>
        <p class="muted small">The thread resumes as soon as you decide. If you deny, it's told why and tries another way.</p>
      }
      @case ('question') {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (answered()) {
          <p class="muted">Sent. Desk picks it up at its next step.</p>
        } @else {
          @if (options().length) {
            <div class="choice-options">
              @for (opt of options(); track opt; let k = $index) {
                <button deskButton [variant]="k === 0 ? 'primary' : 'secondary'" [pending]="busy() === opt" [disabled]="busy() !== null" (click)="answer.emit(opt)">{{ opt }}</button>
              }
            </div>
          }
          <form class="free-answer" (submit)="submitFree($event)">
            <label for="attention-answer">Answer in your own words</label>
            <div class="free-answer-row">
              <input id="attention-answer" type="text" [value]="free()" (input)="setFree($event)" />
              <button deskButton type="submit" [disabled]="!free().trim() || busy() !== null">Send</button>
            </div>
          </form>
        }
        <div class="inspector-actions">
          <button deskButton variant="ghost" (click)="open.emit()">Open conversation <kbd>E</kbd></button>
        </div>
      }
      @case ('paused') {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (item().detail) {<p>{{ item().detail }}</p>}
        <div class="inspector-actions">
          <button deskButton variant="primary" (click)="open.emit()">Open conversation <kbd>E</kbd></button>
          <button deskButton [pending]="busy() === 'dismiss'" [disabled]="busy() !== null" (click)="dismiss.emit()">Resume</button>
        </div>
        <p class="muted small">Resuming lets agents wake each other again.</p>
      }
      @default {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (item().detail) {
          <div class="why">
            <div class="why-row">
              <span class="why-label">{{ detailLabel() }}</span>
              @if (item().kind === 'stalled') {<div deskSafeMarkdown [className]="'why-quote'" [text]="item().detail"></div>} @else {<p>{{ item().detail }}</p>}
            </div>
          </div>
        }
        <div class="inspector-actions">
          <button deskButton variant="primary" (click)="open.emit()">{{ item().kind === 'needs_you' ? 'Open conversation' : 'Open thread' }} <kbd>E</kbd></button>
          <button deskButton [pending]="busy() === 'dismiss'" (click)="dismiss.emit()">Dismiss</button>
        </div>
        <p class="muted small">{{ item().kind === 'needs_you' ? 'Dismiss once it is done. Desk is not told; tell it in the conversation if it needs to know.' : 'Dismissing hides this here. The thread is not changed.' }}</p>
      }
    }
  `,
})
export class Inspector {
  readonly item = input.required<AttentionItem>();
  readonly index = input.required<number>();
  readonly total = input.required<number>();
  readonly now = input.required<number>();
  readonly note = input('');
  readonly busy = input<string | null>(null);
  readonly answered = input(false);
  readonly noteChange = output<string>();
  readonly resolve = output<'approved' | 'denied'>();
  readonly answer = output<string>();
  readonly dismiss = output<void>();
  readonly open = output<void>();

  private readonly sessions = inject(SessionService);
  /** The item's project session, acquired while this Inspector lives (the desktop's useSession). */
  protected readonly s = injectSession(() => this.item().project_id);
  private readonly transcript = computed(() => this.sessions.transcript(this.item().project_id, this.item().agent_id ?? '')());
  protected readonly raw = signal(false);
  protected readonly free = signal('');

  protected readonly code = computed(() => STRIP_CODE[this.item().kind]);
  protected readonly kindName = computed(() => KIND_NAME[this.item().kind]);
  protected readonly when = computed(() => {
    const i = this.item();
    return i.kind === 'approval' ? `paused since ${clock(i.created_at)}` : `waiting ${waited(i.created_at, this.now())}`;
  });
  protected readonly approval = computed(() => {
    const id = this.item().ref.approval_id;
    return id ? this.s().project?.approvals.find((x) => x.id === id) : undefined;
  });
  protected readonly agent = computed(() => {
    const p = this.s().project;
    const id = this.item().agent_id ?? '';
    if (!p) return undefined;
    return p.desk?.id === id ? p.desk : p.threads.find((t) => t.id === id);
  });
  protected readonly lastWords = computed(() => {
    const entries = this.transcript().entries;
    for (let k = entries.length - 1; k >= 0; k--) {
      const e = entries[k]!;
      if (e.kind === 'assistant' && e.text.trim()) return e.text.trim();
    }
    return null;
  });
  protected readonly threadHref = computed(() => {
    const i = this.item();
    return i.ref.thread_id ? href({ name: 'project', id: i.project_id, tab: 'threads', threadId: i.ref.thread_id }) : null;
  });
  protected readonly conversationHref = computed(() => href({ name: 'project', id: this.item().project_id, tab: 'conversation' }));
  protected readonly settingsHref = computed(() => href({ name: 'project', id: this.item().project_id, tab: 'settings' }));
  protected readonly args = computed(() => {
    const a = this.approval();
    return a ? describeArgs(a.tool, a.arguments) : null;
  });
  protected readonly rawArguments = computed(() => this.approval()?.arguments ?? '');
  protected readonly why = computed(() => policyReason(this.item().detail || this.approval()?.reason || ''));
  protected readonly workdir = computed(() => this.agent()?.workspace_path?.split('/').slice(-2).join('/') ?? '—');
  protected readonly requested = computed(() => clock(this.item().created_at));
  protected readonly options = computed(() => this.item().ref.options ?? []);
  protected readonly detailLabel = computed(() => {
    const kind = this.item().kind;
    return kind === 'needs_you' ? 'From the report' : kind === 'failed' ? 'Reason' : 'What the thread said';
  });

  protected setNote(e: Event): void {
    this.noteChange.emit((e.target as HTMLInputElement).value);
  }

  protected setFree(e: Event): void {
    this.free.set((e.target as HTMLInputElement).value);
  }

  /** The free answer, trimmed; the form never submits (form-action 'none'). */
  protected submitFree(e: Event): void {
    e.preventDefault();
    const text = this.free().trim();
    if (text) this.answer.emit(text);
  }
}
