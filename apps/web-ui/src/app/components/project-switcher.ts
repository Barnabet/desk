import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  ElementRef,
  inject,
  Injector,
  input,
  signal,
  viewChild,
  ViewEncapsulation,
} from '@angular/core';
import type { ProjectSummary } from '@desk/protocol';
import { href } from '@desk/ui-core';
import { GlobalStore } from '../core/global.store';
import { RouteService } from '../core/route.service';
import { Unread, unreadIn } from '../core/unread';
import { projectSummaryLine } from '../map/project-summary';

/** Filters by name or goal, then orders: needs you, unread, most recent activity. */
export function switcherList(projects: ProjectSummary[], query: string, seen: Record<string, string>): ProjectSummary[] {
  const q = query.trim().toLowerCase();
  return projects
    .filter((p) => !q || p.project.name.toLowerCase().includes(q) || p.project.goal.toLowerCase().includes(q))
    .sort(
      (a, b) =>
        Number(b.attention_count > 0) - Number(a.attention_count > 0) ||
        Number(unreadIn(b, seen)) - Number(unreadIn(a, seen)) ||
        b.project.updated_at.localeCompare(a.project.updated_at),
    );
}

/** How long the list stays open after the pointer leaves it (moving from the arrow into the list). */
const HOVER_CLOSE_MS = 250;
let nextList = 0;

/**
 * The project switcher in the title bar: "Projects ▾" with no project, or just the ▾ next to the project's name.
 * Hovering the trigger opens it (it closes when the pointer leaves); clicking or ⌘P opens it with the search focused.
 */
@Component({
  selector: 'div[deskProjectSwitcher]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    class: 'switcher',
    '(mouseleave)': 'hoverLeave()',
    '(mouseenter)': 'hoverStay()',
    '(window:keydown)': 'onWindowKey($event)',
    '(window:mousedown)': 'onWindowDown($event)',
  },
  template: `
    <button type="button" class="switcher-btn" aria-label="Switch project (⌘P)" aria-haspopup="dialog" [attr.aria-expanded]="open()" (mouseenter)="hoverOpen()" (click)="toggle()">@if (!currentId()) {<span>Projects</span>}<span aria-hidden="true">▾</span>@if (othersUnread()) {<span class="unread-dot" aria-label="Another project has news"></span>}</button>
    @if (open()) {
      <div class="switcher-pop card" role="dialog" aria-label="Switch project">
        <input
          #search
          type="text"
          role="combobox"
          aria-expanded="true"
          [attr.aria-controls]="listId"
          [attr.aria-activedescendant]="optId(active())"
          aria-label="Find a project"
          placeholder="Find a project"
          [value]="query()"
          (input)="setQuery($event)"
          (keydown)="onKey($event)"
        />
        <ul [id]="listId" role="listbox" aria-label="Projects">
          @for (p of list(); track p.project.id; let i = $index) {
            <li [id]="optId(i)" role="option" [attr.aria-selected]="i === active()" [class.current]="p.project.id === currentId()" (mouseenter)="active.set(i)" (click)="go(p)"><span class="switcher-name">{{ p.project.name }}@if (isUnread(p)) {<span class="unread-dot" aria-label="unread"></span>}</span><span class="switcher-sub">{{ summary(p) }}</span>@if (p.attention_count) {<span class="switcher-needs">{{ p.attention_count }}</span>}</li>
          }
          <li [id]="optId(list().length)" role="option" [attr.aria-selected]="active() === list().length" class="switcher-new" (mouseenter)="active.set(list().length)" (click)="go(undefined)">+ New project</li>
        </ul>
      </div>
    }
  `,
})
export class ProjectSwitcher {
  readonly currentId = input<string | null>(null);
  private readonly global = inject(GlobalStore).state;
  private readonly unread = inject(Unread);
  private readonly routes = inject(RouteService);
  private readonly injector = inject(Injector);
  private readonly host: HTMLElement = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly search = viewChild<ElementRef<HTMLInputElement>>('search');
  protected readonly open = signal(false);
  /** Opened by hovering (closes on leave) rather than by click or ⌘P (closes on an outside click or Escape). */
  private byHover = false;
  private closeTimer: ReturnType<typeof setTimeout> | undefined;
  protected readonly query = signal('');
  protected readonly active = signal(0);
  protected readonly listId = `switcher-list-${++nextList}`;
  protected readonly list = computed(() => switcherList(this.global().overview, this.query(), this.unread.seen()));
  protected readonly othersUnread = computed(() => {
    const seen = this.unread.seen();
    return this.global().overview.some((p) => p.project.id !== this.currentId() && unreadIn(p, seen));
  });

  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.closeTimer));
  }

  protected optId(i: number): string {
    return `${this.listId}-${i}`;
  }

  protected isUnread(p: ProjectSummary): boolean {
    return unreadIn(p, this.unread.seen());
  }

  protected summary(p: ProjectSummary): string {
    return projectSummaryLine(p);
  }

  protected onWindowKey(e: KeyboardEvent): void {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'p') {
      e.preventDefault();
      if (this.open()) this.open.set(false);
      else this.show(false);
    }
  }

  protected onWindowDown(e: MouseEvent): void {
    if (this.open() && !this.host.contains(e.target as Node)) this.open.set(false);
  }

  protected hoverOpen(): void {
    clearTimeout(this.closeTimer);
    if (!this.open()) this.show(true);
  }

  protected hoverLeave(): void {
    if (!this.byHover) return;
    clearTimeout(this.closeTimer);
    this.closeTimer = setTimeout(() => this.open.set(false), HOVER_CLOSE_MS);
  }

  protected hoverStay(): void {
    if (this.open()) clearTimeout(this.closeTimer);
  }

  protected toggle(): void {
    clearTimeout(this.closeTimer);
    if (this.open() && this.byHover) {
      // A click on a list opened by hovering keeps it open and moves focus into the search.
      this.byHover = false;
      this.search()?.nativeElement.focus();
      return;
    }
    if (this.open()) this.open.set(false);
    else this.show(false);
  }

  protected setQuery(e: Event): void {
    this.query.set((e.target as HTMLInputElement).value);
    this.active.set(0);
  }

  protected go(p: ProjectSummary | undefined): void {
    this.open.set(false);
    this.routes.navigate(p ? href({ name: 'project', id: p.project.id, tab: 'conversation' }) : href({ name: 'map', newProject: true }));
  }

  protected onKey(e: KeyboardEvent): void {
    const n = this.list().length + 1;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      this.active.update((a) => (a + 1) % n);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      this.active.update((a) => (a - 1 + n) % n);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      this.go(this.list()[this.active()]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      this.open.set(false);
    }
  }

  private show(byHover: boolean): void {
    this.byHover = byHover;
    this.query.set('');
    this.active.set(0);
    this.open.set(true);
    if (!byHover) afterNextRender(() => this.search()?.nativeElement.focus(), { injector: this.injector });
  }
}
