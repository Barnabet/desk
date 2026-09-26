import { ChangeDetectionStrategy, Component, computed, effect, inject, input, linkedSignal, signal, untracked, ViewEncapsulation } from '@angular/core';
import type { AttentionItem } from '@desk/protocol';
import { href, plural, rackOrder, STRIP_CODE, waited } from '@desk/ui-core';
import { EmptyState } from '../components/empty-state';
import { ToastService } from '../components/toast';
import { DeskBridge, DeskCallError } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { NowService } from '../core/now.service';
import { RouteService } from '../core/route.service';
import { Inspector } from './inspector';
import { StripRack } from './strip-rack';

/** Who decided an approval first, for the 409 toast. */
const WHO: Record<string, string> = { user: 'you, in another window', desk: 'Desk', system: 'Desk (the thread was stopped)' };

/** Where E goes: the thread for approvals and stuck threads, the conversation for everything else. */
function openTarget(i: AttentionItem): string {
  if ((i.kind === 'stalled' || i.kind === 'failed' || i.kind === 'approval') && i.ref.thread_id) return href({ name: 'project', id: i.project_id, tab: 'threads', threadId: i.ref.thread_id });
  return href({ name: 'project', id: i.project_id, tab: 'conversation' });
}

const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);

/** Everything that needs you, as flight strips in a rack, with the selected strip inspected on the right. */
@Component({
  selector: 'div[deskAttentionScreen]',
  imports: [EmptyState, Inspector, StripRack],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'attention', '(window:keydown)': 'onKey($event)' },
  template: `
    <div class="attention-left">
      <header class="attention-head">
        <h1>Needs you</h1>
        <p class="muted">{{ summary() }}</p>
      </header>
      <section deskStripRack [bays]="rack().bays" [now]="now()" [selectedId]="selected()?.id ?? null" [threadTitle]="threadTitle()" (pick)="select($event)"></section>
      <p class="keys mono">J K move · ⌘⏎ approve · ⌘⌫ deny · E open</p>
      <div class="strip-legend" aria-hidden="true">
        <span><span class="strip-code-badge code-approval">{{ codes.approval }}</span>Approval</span>
        <span><span class="strip-code-badge code-question">{{ codes.question }}</span>Question from Desk</span>
        <span><span class="strip-code-badge code-needs_you">{{ codes.needs_you }}</span>From a report</span>
        <span><span class="strip-code-badge code-stalled">{{ codes.stalled }}</span>Stalled thread</span>
        <span><span class="strip-code-badge code-failed">{{ codes.failed }}</span>Failed thread</span>
        <span><span class="strip-code-badge code-paused">{{ codes.paused }}</span>Paused project</span>
        <span class="muted">Bar = wait, 0–2h</span>
      </div>
    </div>
    <div class="attention-right">
      @for (s of shown(); track s.id) {
        <article
          deskInspector
          [item]="s"
          [index]="index()"
          [total]="rack().flat.length"
          [now]="now()"
          [note]="note()"
          [busy]="busy()"
          [answered]="answered().has(s.id)"
          (noteChange)="note.set($event)"
          (resolve)="resolve($event)"
          (answer)="answer($event)"
          (dismiss)="dismiss()"
          (open)="open()"
        ></article>
      } @empty {
        <div deskEmptyState title="All clear" body="Approvals, questions, hand-offs from reports and stuck threads land here."><a href="#/map">Back to the map</a></div>
      }
    </div>
  `,
})
export class AttentionScreen {
  /** The route's `?item=`: the strip to inspect. */
  readonly itemId = input<string | undefined>();

  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly toasts = inject(ToastService);
  private readonly global = inject(GlobalStore).state;
  protected readonly now = inject(NowService).now;
  protected readonly codes = STRIP_CODE;

  /** Bay by bay, oldest first within a bay: the rack's order, which is also the J/K order. */
  protected readonly rack = computed(() => rackOrder(this.global().attention));
  protected readonly threadTitle = computed(() => {
    const titles = new Map(this.global().overview.flatMap((p) => p.threads.map((t) => [t.id, t.title] as const)));
    return (id: string): string | null => titles.get(id) ?? null;
  });
  /** React's lastIndex ref: the last position the route named, kept when that item leaves the rack. */
  private lastIndex = 0;
  private readonly found = computed(() => {
    const id = this.itemId();
    return this.rack().flat.findIndex((i) => i.id === id);
  });
  protected readonly index = computed(() => {
    const found = this.found();
    return found >= 0 ? found : Math.min(this.lastIndex, this.rack().flat.length - 1);
  });
  protected readonly selected = computed<AttentionItem | undefined>(() => {
    const index = this.index();
    return index >= 0 ? this.rack().flat[index] : undefined;
  });
  /** The selected item as a one-item list, so a new selection mounts a fresh Inspector (React's key). */
  protected readonly shown = computed(() => {
    const s = this.selected();
    return s ? [s] : [];
  });
  /** The note to the thread; cleared when the selection changes. */
  protected readonly note = linkedSignal({ source: () => this.selected()?.id, computation: (): string => '' });
  /** The decision, option, text or 'dismiss' on its way; cleared when the selection changes. */
  protected readonly busy = linkedSignal<string | undefined, string | null>({ source: () => this.selected()?.id, computation: () => null });
  protected readonly answered = signal<ReadonlySet<string>>(new Set());
  protected readonly summary = computed(() => {
    const flat = this.rack().flat;
    const first = flat[0];
    if (!first) return 'Nothing is waiting on you.';
    const projects = new Set(this.global().attention.map((i) => i.project_id)).size;
    const oldest = flat.reduce((m, i) => (i.created_at < m ? i.created_at : m), first.created_at);
    return `${plural(flat.length, 'item')} across ${plural(projects, 'project')} · oldest ${waited(oldest, this.now())}`;
  });

  constructor() {
    // Keep the selection in the route: remember where it is, or name the item shown when the route names none (or a gone one).
    effect(() => {
      const found = this.found();
      const selected = this.selected();
      if (found >= 0) this.lastIndex = found;
      else if (selected) {
        const item = selected.id;
        untracked(() => this.routes.replace({ name: 'attention', item }));
      }
    });
  }

  protected select(id: string): void {
    this.routes.replace({ name: 'attention', item: id });
  }

  protected async resolve(decision: 'approved' | 'denied'): Promise<void> {
    const selected = this.selected();
    const approvalId = selected?.kind === 'approval' ? selected.ref.approval_id : undefined;
    if (!selected || !approvalId || this.busy()) return;
    const note = this.note().trim();
    this.busy.set(decision);
    try {
      await this.bridge.call('approvals.resolve', { id: approvalId, decision, ...(note ? { note } : {}) });
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 409) {
        const all = await this.bridge.call('approvals.list', { projectId: selected.project_id }).catch(() => null);
        const by = all?.find((x) => x.id === approvalId)?.resolved_by;
        this.toasts.toast({ tone: 'info', message: `Already decided by ${by ? (WHO[by] ?? by) : 'someone else'}.` });
      } else this.toasts.error(err);
      this.busy.set(null);
    }
  }

  protected async answer(text: string): Promise<void> {
    const selected = this.selected();
    if (!selected) return;
    this.busy.set(text);
    try {
      await this.bridge.call('projects.send', { id: selected.project_id, text });
      this.answered.update((s) => new Set(s).add(selected.id));
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(null);
    }
  }

  protected async dismiss(): Promise<void> {
    const selected = this.selected();
    if (!selected) return;
    this.busy.set('dismiss');
    try {
      await this.bridge.call('attention.dismiss', { id: selected.id });
    } catch (err) {
      this.toasts.error(err);
      this.busy.set(null);
    }
  }

  protected open(): void {
    const selected = this.selected();
    if (selected) this.routes.navigate(openTarget(selected));
  }

  protected onKey(e: KeyboardEvent): void {
    // Autofill and some extensions send keydown events without a key.
    if (typeof e.key !== 'string') return;
    const flat = this.rack().flat;
    if (!flat.length) return;
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key === 'Enter') {
      e.preventDefault();
      void this.resolve('approved');
    } else if (mod && e.key === 'Backspace') {
      e.preventDefault();
      void this.resolve('denied');
    } else if (!mod && !e.altKey && !typing(e.target)) {
      const k = e.key.toLowerCase();
      if (k === 'j' || k === 'k') {
        e.preventDefault();
        const next = flat[Math.max(0, Math.min(flat.length - 1, this.index() + (k === 'j' ? 1 : -1)))];
        if (next) this.select(next.id);
      } else if (k === 'e') {
        e.preventDefault();
        this.open();
      }
    }
  }
}
