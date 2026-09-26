import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, effect, inject, input, linkedSignal, signal } from '@angular/core';
import type { MemoryKind } from '@desk/protocol';
import { activeEntries, chainOf, clock, href, MEMORY_KINDS, memoryFromEvents, originAgent, plural, type MemoryEntry } from '@desk/ui-core';
import { Button } from '../components/button';
import { ConfirmDialog } from '../components/confirm-dialog';
import { EmptyState } from '../components/empty-state';
import { SafeMarkdown } from '../components/safe-markdown';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { RouteService } from '../core/route.service';
import { injectSession } from '../core/session.service';

const val = (e: Event): string => (e.target as HTMLInputElement | HTMLTextAreaElement).value;

/** One memory entry: its text (or the correction box), who wrote it, its earlier versions, Correct and Delete (MemoryScreen.tsx's Entry). */
@Component({
  selector: 'li[deskMemoryEntry]',
  imports: [Button, ConfirmDialog, SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'memory-entry card', '[attr.aria-label]': 'ariaLabel()' },
  template: `
    @if (editing()) {
      <div class="field">
        <label [attr.for]="'mem-' + m().id">Correct this entry</label>
        <textarea [id]="'mem-' + m().id" class="textarea" rows="3" [value]="draft()" (input)="draft.set(val($event))"></textarea>
        <div class="actions">
          <button deskButton size="sm" variant="primary" [pending]="busy() === 'save'" (click)="save()">Save correction</button>
          <button deskButton size="sm" variant="ghost" (click)="cancelEdit()">Cancel</button>
          <span class="muted small">The old version is kept in the history.</span>
        </div>
      </div>
    } @else {
      <div deskSafeMarkdown [className]="'memory-content'" [text]="m().content"></div>
    }
    <div class="memory-meta small muted">
      <span>@if (agentOf(m().source); as agent) {<a [href]="threadHref(agent)">{{ label()(agent) }}</a>} @else {you} · {{ clock(m().ts) }}</span>
      @if (chain().length) {
        <button type="button" class="link small" [attr.aria-expanded]="showChain()" (click)="showChain.set(!showChain())">Corrected {{ plural(chain().length, 'time') }}</button>
      }
      <span class="grow"></span>
      @if (!editing()) {
        <button deskButton size="sm" variant="ghost" (click)="editing.set(true)">Correct</button>
        <button deskButton size="sm" variant="ghost" [pending]="busy() === 'delete'" (click)="confirming.set(true)">Delete</button>
      }
    </div>
    @if (showChain()) {
      <ol class="memory-chain" aria-label="Earlier versions">
        @for (c of chain(); track c.id) {
          <li><span class="memory-old">{{ c.content }}</span><span class="muted small">&ngsp;· @if (agentOf(c.source); as agent) {<a [href]="threadHref(agent)">{{ label()(agent) }}</a>} @else {you} · {{ clock(c.ts) }}</span></li>
        }
      </ol>
    }
    @if (confirming()) {
      <div deskConfirmDialog title="Delete this memory?" confirmLabel="Delete" danger (confirm)="remove()" (cancel)="confirming.set(false)">Desk and its threads stop seeing it. To change it instead, use Correct.</div>
    }
  `,
})
export class Entry {
  readonly projectId = input.required<string>();
  readonly m = input.required<MemoryEntry>();
  /** The versions this entry replaced, newest first. */
  readonly chain = input.required<MemoryEntry[]>();
  /** A thread's title by id ("Desk", or "a thread" when unknown). */
  readonly label = input.required<(id: string) => string>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly editing = signal(false);
  /** The correction being typed; a fresh fold of the same entry keeps it. */
  protected readonly draft = linkedSignal(() => this.m().content);
  protected readonly busy = signal<'save' | 'delete' | null>(null);
  protected readonly showChain = signal(false);
  protected readonly confirming = signal(false);
  protected readonly ariaLabel = computed(() => `${this.m().kind}: ${this.m().content.slice(0, 80)}`);
  protected readonly agentOf = originAgent;
  protected readonly clock = clock;
  protected readonly plural = plural;
  protected readonly val = val;

  protected threadHref(agentId: string): string {
    return href({ name: 'project', id: this.projectId(), tab: 'threads', threadId: agentId });
  }

  protected cancelEdit(): void {
    this.editing.set(false);
    this.draft.set(this.m().content);
  }

  protected async save(): Promise<void> {
    const content = this.draft().trim();
    if (!content || content === this.m().content) {
      this.editing.set(false);
      return;
    }
    this.busy.set('save');
    try {
      await this.bridge.call('memory.correct', { projectId: this.projectId(), memoryId: this.m().id, update: { content } });
      this.editing.set(false);
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(null);
    }
  }

  /** Stays busy on success: the entry leaves when its memory.deleted event lands. */
  protected async remove(): Promise<void> {
    this.confirming.set(false);
    this.busy.set('delete');
    try {
      await this.bridge.call('memory.remove', { projectId: this.projectId(), memoryId: this.m().id });
    } catch (err) {
      this.toasts.error(err);
      this.busy.set(null);
    }
  }
}

/** What Desk remembers for this project: grouped by kind, searchable, and correctable by you (MemoryScreen.tsx). */
@Component({
  selector: 'div[deskMemoryScreen]',
  imports: [Button, EmptyState, Entry],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'page', '[class]': 'hostClass()' },
  template: `
    @switch (view()) {
      @case ('loading') {Loading…}
      @case ('error') {
        <div deskEmptyState title="Couldn't load this project" [body]="s().error"></div>
      }
      @default {
        <div class="knowledge-head">
          <h1 class="title">Memory</h1>
          <span class="muted">{{ plural(active().length, 'entry', 'entries') }}. Desk and its threads read these before they work.</span>
        </div>
        <form class="card memory-add" (submit)="add($event)">
          <label for="memory-new">Remember something</label>
          <div class="memory-add-row">
            <select aria-label="Kind" class="select" (change)="pickKind($event)">
              @for (k of kinds; track k.kind) {
                <option [value]="k.kind" [selected]="k.kind === kind()">{{ k.kind }}</option>
              }
            </select>
            <input id="memory-new" type="text" [value]="content()" (input)="content.set(val($event))" placeholder="e.g. We never discount annual plans" />
            <button deskButton type="submit" variant="primary" [pending]="adding()" [disabled]="!content().trim()">Add</button>
          </div>
        </form>
        <input class="knowledge-search" type="search" aria-label="Search memory" placeholder="Search memory" [value]="query()" (input)="search(val($event))" />
        @if (hits() && !shown().length) {
          <p class="muted">No entries match “{{ query().trim() }}”.</p>
        }
        @if (!active().length) {
          <div deskEmptyState title="Nothing remembered yet" [body]="'Desk writes down decisions, facts and preferences as it works. You can add and correct them here.'"></div>
        }
        @if (hits()) {
          <ul class="memory-list" aria-label="Search results">
            @for (m of shown(); track m.id) {
              <li deskMemoryEntry [projectId]="projectId()" [m]="m" [chain]="chainFor()(m.id)" [label]="label()"></li>
            }
          </ul>
        } @else {
          @for (g of groups(); track g.kind) {
            <section [attr.aria-labelledby]="'mem-group-' + g.kind">
              <h2 [id]="'mem-group-' + g.kind" class="memory-group">{{ g.title }} <span class="muted">{{ g.list.length }}</span></h2>
              <ul class="memory-list">
                @for (m of g.list; track m.id) {
                  <li deskMemoryEntry [projectId]="projectId()" [m]="m" [chain]="chainFor()(m.id)" [label]="label()"></li>
                }
              </ul>
            </section>
          }
        }
      }
    }
  `,
})
export class MemoryScreen {
  readonly projectId = input.required<string>();
  /** The route's search (`?q=`). */
  readonly q = input<string>();

  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly toasts = inject(ToastService);
  protected readonly s = injectSession(this.projectId);
  private readonly events = computed(() => this.s().events);
  private readonly project = computed(() => this.s().project);

  protected readonly all = computed(() => memoryFromEvents(this.events()));
  protected readonly active = computed(() => activeEntries(this.all()));
  /** Only a new count re-runs the search (React's `active.length` dependency). */
  private readonly activeCount = computed(() => this.active().length);
  protected readonly query = linkedSignal(() => this.q() ?? '');
  protected readonly hits = signal<string[] | null>(null);
  protected readonly kind = signal<MemoryKind>('fact');
  protected readonly content = signal('');
  protected readonly adding = signal(false);
  protected readonly kinds = MEMORY_KINDS;
  protected readonly plural = plural;
  protected readonly val = val;

  protected readonly label = computed(() => {
    const p = this.project();
    return (id: string): string => p?.threads.find((t) => t.id === id)?.title ?? (p?.desk?.id === id ? 'Desk' : 'a thread');
  });
  /** Each entry's earlier versions, the same array for as long as the fold is the same. */
  protected readonly chainFor = computed(() => {
    const all = this.all();
    const cache = new Map<string, MemoryEntry[]>();
    return (id: string): MemoryEntry[] => {
      let chain = cache.get(id);
      if (!chain) {
        chain = chainOf(all, id);
        cache.set(id, chain);
      }
      return chain;
    };
  });
  protected readonly shown = computed(() => {
    const hits = this.hits();
    if (!hits) return this.active();
    const byId = new Map(this.active().map((m) => [m.id, m]));
    return hits.map((id) => byId.get(id)).filter((m): m is MemoryEntry => !!m);
  });
  protected readonly groups = computed(() =>
    MEMORY_KINDS.map((k) => ({ kind: k.kind, title: k.title, list: this.shown().filter((m) => m.kind === k.kind) })).filter((g) => g.list.length),
  );

  protected readonly view = computed<'loading' | 'error' | 'ready'>(() => {
    const status = this.s().status;
    return status === 'loading' ? 'loading' : status === 'ready' ? 'ready' : 'error';
  });
  /** The class beside the fixed `page`. */
  protected readonly hostClass = computed(() => ({ loading: 'muted', error: '', ready: 'memory' })[this.view()]);

  constructor() {
    effect((onCleanup) => {
      const term = this.query().trim();
      const projectId = this.projectId();
      this.activeCount();
      if (!term) {
        this.hits.set(null);
        return;
      }
      let live = true;
      const timer = setTimeout(() => {
        this.bridge.call('memory.list', { projectId, q: term }).then(
          (rows) => {
            if (live) this.hits.set(rows.map((r) => r.id));
          },
          (err: unknown) => {
            if (!live) return;
            this.toasts.error(err);
            this.hits.set([]);
          },
        );
      }, 250);
      onCleanup(() => {
        live = false;
        clearTimeout(timer);
      });
    });
  }

  protected pickKind(e: Event): void {
    this.kind.set((e.target as HTMLSelectElement).value as MemoryKind);
  }

  protected search(value: string): void {
    this.query.set(value);
    this.routes.replace({ name: 'project', id: this.projectId(), tab: 'memory', ...(value.trim() ? { q: value } : {}) });
  }

  protected async add(e: Event): Promise<void> {
    e.preventDefault();
    const content = this.content().trim();
    if (!content) return;
    this.adding.set(true);
    try {
      await this.bridge.call('memory.add', { projectId: this.projectId(), entry: { kind: this.kind(), content } });
      this.content.set('');
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.adding.set(false);
    }
  }
}
