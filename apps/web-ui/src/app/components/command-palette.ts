import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  Injectable,
  signal,
  viewChild,
  ViewEncapsulation,
  type Signal,
} from '@angular/core';
import type { SkillSummary } from '@desk/client';
import { clip, type ArtifactKind, type BuiltinSkillInfo, type CatalogItem } from '@desk/protocol';
import { GROUP_ORDER, href, rankPalette, type PaletteItem } from '@desk/ui-core';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { RouteService } from '../core/route.service';
import { ToastService } from './toast';

type Loaded = {
  skills: Array<{ key: string; name: string; scope: string; description: string; project: string | null }>;
  library: Array<{ projectId: string; project: string; path: string; title: string; kind: ArtifactKind }>;
  catalog: CatalogItem[];
  builtins: BuiltinSkillInfo[];
};
const MAX_MEMORY_PROJECTS = 8;

const GO: PaletteItem[] = [
  { id: 'go:map', group: 'Go to', title: 'Map', detail: 'All projects', route: href({ name: 'map' }) },
  { id: 'go:attention', group: 'Go to', title: 'Needs you', detail: 'Approvals, questions and hand-offs', keywords: 'attention approvals', route: href({ name: 'attention' }) },
  { id: 'go:skills', group: 'Go to', title: 'Skills', route: href({ name: 'skills' }) },
  { id: 'go:catalog', group: 'Go to', title: 'Skill catalog', detail: 'Install reviewed skills', keywords: 'install add', route: href({ name: 'catalog' }) },
  { id: 'go:system', group: 'Go to', title: 'System', detail: 'deskd, models, usage', keywords: 'settings daemon endpoint', route: href({ name: 'system' }) },
  { id: 'go:new', group: 'Go to', title: 'New project', keywords: 'create', route: href({ name: 'map', newProject: true }) },
];

/**
 * Whether the ⌘K palette is open. While App shows the shell, ⌘K / Ctrl-K toggles it from anywhere on the page (the
 * desktop's CommandPalette keeps this state itself, and only its Shell mounts it); App renders `div[deskCommandPalette]`
 * only while it is open, so a closed palette adds no element.
 */
@Injectable({ providedIn: 'root' })
export class PaletteToggle {
  private readonly value = signal(false);
  /** Off (the start) leaves ⌘K to the browser: while signed out and during onboarding, App shows no shell. */
  private readonly enabled = signal(false);
  readonly open: Signal<boolean> = this.value.asReadonly();

  constructor() {
    const onKey = (e: KeyboardEvent) => {
      if (!this.enabled()) return;
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        this.toggle();
      }
    };
    window.addEventListener('keydown', onKey);
    inject(DestroyRef).onDestroy(() => window.removeEventListener('keydown', onKey));
  }

  /** App turns ⌘K on while it shows the shell, and off when it does not, which also closes the palette. */
  setEnabled(on: boolean): void {
    this.enabled.set(on);
    if (!on) this.value.set(false);
  }

  toggle(): void {
    this.value.update((open) => !open);
  }

  close(): void {
    this.value.set(false);
  }
}

let nextList = 0;

/** ⌘K: jump to a place, project, thread, skill, catalog entry or library file, or search every project's memory. */
@Component({
  selector: 'div[deskCommandPalette]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'palette-backdrop', '(mousedown)': 'onBackdrop($event)' },
  template: `
    <div class="palette card" role="dialog" aria-modal="true" aria-label="Search Desk">
      <input
        #box
        type="text"
        role="combobox"
        aria-expanded="true"
        [attr.aria-controls]="listId"
        [attr.aria-activedescendant]="shown().length ? optId(active()) : null"
        aria-label="Search projects, threads, skills, files and memory"
        placeholder="Search projects, threads, skills, files and memory"
        [value]="query()"
        (input)="setQuery($event)"
        (keydown)="onKey($event)"
      />
      <ul [id]="listId" role="listbox" aria-label="Results">
        @for (g of groups(); track g.group) {
          <li role="presentation" class="palette-group">{{ g.group }}</li>
          @for (row of g.rows; track row.item.id) {
            <li [id]="optId(row.index)" role="option" [attr.aria-selected]="row.index === active()" (mouseenter)="active.set(row.index)" (click)="go(row.item)"><span class="palette-title">{{ row.item.title }}</span>@if (row.item.detail) {<span class="palette-detail">{{ row.item.detail }}</span>}</li>
          }
        }
        @if (!shown().length) {
          <li role="presentation" class="palette-empty">{{ searching() ? 'Nothing found.' : 'Keep typing…' }}</li>
        }
      </ul>
      <div class="palette-foot muted small">↑↓ move · ⏎ open · esc close{{ searching() ? ' · memory is searched in every project' : '' }}</div>
    </div>
  `,
})
export class CommandPalette {
  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly toggle = inject(PaletteToggle);
  private readonly global = inject(GlobalStore).state;
  private readonly toasts = inject(ToastService);
  private readonly host: HTMLElement = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly box = viewChild.required<ElementRef<HTMLInputElement>>('box');
  protected readonly listId = `palette-list-${++nextList}`;
  protected readonly query = signal('');
  protected readonly active = signal(0);
  private readonly loaded = signal<Loaded | null>(null);
  private readonly memory = signal<PaletteItem[]>([]);
  protected readonly searching = computed(() => this.query().trim().length >= 3);
  /** The projects memory is searched in: a desk:global push that changes anything else leaves a pending search alone. */
  private readonly memoryProjects = computed(() => this.global().overview.slice(0, MAX_MEMORY_PROJECTS).map((p) => ({ id: p.project.id, name: p.project.name })), {
    equal: (a, b) => a.length === b.length && a.every((p, i) => p.id === b[i]!.id && p.name === b[i]!.name),
  });

  private readonly items = computed(() => {
    const all: PaletteItem[] = [...GO];
    for (const p of this.global().overview) {
      all.push({ id: `project:${p.project.id}`, group: 'Projects', title: p.project.name, detail: p.project.goal, route: href({ name: 'project', id: p.project.id, tab: 'conversation' }) });
      for (const t of p.threads)
        all.push({ id: `thread:${t.id}`, group: 'Threads', title: t.title ?? 'Untitled thread', detail: `${p.project.name} · ${t.status}`, route: href({ name: 'project', id: p.project.id, tab: 'threads', threadId: t.id }) });
    }
    const loaded = this.loaded();
    for (const s of loaded?.skills ?? []) all.push({ id: `skill:${s.key}`, group: 'Skills', title: s.name, detail: `${s.project ?? 'Global'} · ${s.description}`, route: href({ name: 'skills', skill: s.key }) });
    for (const c of loaded?.catalog ?? []) {
      const global = c.installs.find((i) => i.scope === 'global');
      if (global?.state === 'installed' || global?.state === 'name_taken') continue;
      const verb = global?.state === 'update_available' ? 'Update' : 'Install';
      all.push({ id: `catalog:${c.id}`, group: 'Catalog', title: `${verb} ${c.title}`, detail: c.summary, keywords: `${c.id} ${c.category}`, route: href({ name: 'catalog', review: c.id }) });
    }
    // Turning a built-in skill off or on runs in place (`run`), as master's palette does; a damaged one is left out.
    for (const b of loaded?.builtins ?? []) {
      if (b.broken) continue;
      all.push({
        id: `builtin:${b.name}`,
        group: 'Built-in skills',
        title: `${b.enabled ? 'Turn off' : 'Turn on'} ${b.title}`,
        detail: b.summary,
        keywords: `${b.name} built-in skill ${b.enabled ? 'disable off' : 'enable on'}`,
        route: href({ name: 'skills', skill: `builtin:${b.name}` }),
        run: async () => {
          await this.bridge.call('builtins.setEnabled', { name: b.name, enabled: !b.enabled });
          this.toasts.toast({ tone: 'info', message: `${b.enabled ? 'Turned off' : 'Turned on'} ${b.title}.` });
        },
      });
    }
    for (const a of loaded?.library ?? [])
      all.push({ id: `lib:${a.projectId}:${a.path}`, group: 'Library', title: a.title, detail: `${a.project} · ${a.path}`, keywords: a.kind, route: href({ name: 'project', id: a.projectId, tab: 'library', file: a.path }) });
    return [...all, ...this.memory()];
  });

  protected readonly shown = computed(() => rankPalette(this.items(), this.query()));

  /** The shown items by group, each with its index in `shown` (the option ids and `active` count across groups). */
  protected readonly groups = computed(() => {
    const shown = this.shown();
    let index = -1;
    return GROUP_ORDER.map((group) => ({ group, rows: shown.filter((i) => i.group === group).map((item) => ({ item, index: ++index })) })).filter((g) => g.rows.length);
  });

  constructor() {
    let live = true;
    inject(DestroyRef).onDestroy(() => (live = false));
    // Loaded once per opening; the overview at that moment is enough.
    const projects = this.global().overview.map((p) => ({ id: p.project.id, name: p.project.name }));
    void Promise.all([
      this.bridge.call('skills.list', {}).catch(() => [] as SkillSummary[]),
      Promise.all(projects.map((p) => this.bridge.call('skills.list', { projectId: p.id }).catch(() => [] as SkillSummary[]))),
      Promise.all(projects.map((p) => this.bridge.call('library.list', { projectId: p.id }).catch(() => []))),
      this.bridge.call('catalog.list', {}).catch(() => [] as CatalogItem[]),
      this.bridge.call('builtins.list', {}).catch(() => [] as BuiltinSkillInfo[]),
    ]).then(([global, perProject, libraries, catalog, builtins]) => {
      if (!live) return;
      const skills: Loaded['skills'] = global.map((s) => ({ key: `global:${s.name}`, name: s.name, scope: 'global', description: s.description, project: null }));
      perProject.forEach((list, i) => {
        for (const s of list) if (s.scope === 'project') skills.push({ key: `project:${projects[i]!.id}:${s.name}`, name: s.name, scope: 'project', description: s.description, project: projects[i]!.name });
      });
      const library = libraries.flatMap((list, i) => list.map((a) => ({ projectId: projects[i]!.id, project: projects[i]!.name, path: a.path, title: a.title, kind: a.kind })));
      this.loaded.set({ skills, library, catalog, builtins });
    });
    afterNextRender(() => this.box().nativeElement.focus());
    // Memory is searched on the server, in the first projects, once the query has three characters and rests for 250 ms.
    effect((onCleanup) => {
      const q = this.query().trim();
      const searched = this.memoryProjects();
      if (q.length < 3) {
        this.memory.set([]);
        return;
      }
      let current = true;
      const timer = setTimeout(() => {
        void Promise.all(searched.map((p) => this.bridge.call('memory.list', { projectId: p.id, q }).catch(() => []))).then((results) => {
          if (!current) return;
          this.memory.set(
            results.flatMap((rows, i) =>
              rows.slice(0, 2).map((m) => ({
                id: `memory:${m.id}`,
                group: 'Memory' as const,
                title: clip(m.content, 90),
                detail: `${searched[i]!.name} · ${m.kind}`,
                route: href({ name: 'project', id: searched[i]!.id, tab: 'memory', q }),
              })),
            ),
          );
        });
      }, 250);
      onCleanup(() => {
        current = false;
        clearTimeout(timer);
      });
    });
  }

  protected optId(i: number): string {
    return `${this.listId}-${i}`;
  }

  protected setQuery(e: Event): void {
    this.query.set((e.target as HTMLInputElement).value);
    this.active.set(0);
  }

  protected go(item: PaletteItem | undefined): void {
    if (!item) return;
    this.toggle.close();
    if (item.run) void item.run().catch((err: unknown) => this.toasts.error(err));
    else this.routes.navigate(item.route);
  }

  protected onKey(e: KeyboardEvent): void {
    const shown = this.shown();
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      this.active.update((a) => Math.min(shown.length - 1, a + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      this.active.update((a) => Math.max(0, a - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      this.go(shown[this.active()]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      this.toggle.close();
    }
  }

  protected onBackdrop(e: MouseEvent): void {
    if (e.target === this.host) this.toggle.close();
  }
}
