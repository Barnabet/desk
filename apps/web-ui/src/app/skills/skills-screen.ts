import { ChangeDetectionStrategy, Component, DestroyRef, ViewEncapsulation, booleanAttribute, computed, forwardRef, inject, input, signal } from '@angular/core';
import type { SkillDetail } from '@desk/client';
import { parseSkillKey, skillKey, type SkillRef } from '@desk/ui-core';
import { Button } from '../components/button';
import { EmptyState } from '../components/empty-state';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { RouteService } from '../core/route.service';
import { projectTone } from '../map/project-summary';
import { AskDesk } from './ask-desk';
import { BuiltinGroup } from './builtins/builtin-group';
import { BuiltinPanel } from './builtins/builtin-panel';
import { injectBuiltins, parseBuiltinKey } from './builtins/data';
import { CatalogView, injectCatalogLayout, LayoutSwitch } from './catalog/catalog-view';
import { catalogIndex, injectCatalog } from './catalog/data';
import { ReviewSheet } from './catalog/review-sheet';
import { injectSkills } from './data';
import { ImportSheet } from './import-sheet';
import { SkillEditor } from './skill-editor';
import { SkillList } from './skill-list';
import { SkillPanel } from './skill-panel';
import { SkillsRefresh } from './skills-refresh';
import { SkillsMapView } from './skills-map-view';

type View = 'map' | 'list';
type Filter = 'all' | 'used' | 'shadowed';

const VIEW_KEY = 'desk.skillsView';
const FILTERS: Array<[Filter, string]> = [
  ['all', 'All'],
  ['used', 'In use now'],
  ['shadowed', 'Shadowed'],
];

function storedView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'map';
  } catch {
    return 'map';
  }
}

/**
 * Every skill Desk and its threads can use: Desk's built-in skills, a map (or list) with global, project and shadowed
 * skills, and what's in use now; and the catalog of pinned skills to install, each reviewed first (SkillsScreen.tsx).
 */
@Component({
  selector: 'div[deskSkillsScreen]',
  imports: [AskDesk, BuiltinGroup, BuiltinPanel, Button, CatalogView, EmptyState, ImportSheet, LayoutSwitch, ReviewSheet, SkillEditor, SkillList, SkillPanel, SkillsMapView],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'skills', '[class.with-panel]': '!!ref() || !!builtin()' },
  // A panel's late delete, restore or copy reaches the screen through this once the panel itself is gone.
  providers: [{ provide: SkillsRefresh, useExisting: forwardRef(() => SkillsScreen) }],
  template: `
    <div class="skills-main">
      <div class="skills-head">
        <h1 class="title">{{ catalog() ? 'Skill catalog' : 'Skill map' }}</h1>
        <p class="muted">{{ catalog() ? catalogBlurb : mapBlurb }}</p>
        <div class="skills-controls">
          <div class="segmented" role="group" aria-label="View">
            <button type="button" [attr.aria-pressed]="!catalog() && view() === 'map'" (click)="showView('map')">Map</button>
            <button type="button" [attr.aria-pressed]="!catalog() && view() === 'list'" (click)="showView('list')">List</button>
            <button type="button" [attr.aria-pressed]="catalog()" (click)="showCatalog()">Catalog</button>
          </div>
          @if (catalog()) {
            <div deskLayoutSwitch [layout]="layout.layout()" (changed)="layout.set($event)"></div>
          } @else {
            <div class="chips" role="group" aria-label="Show">
              @for (f of filters; track f[0]) {
                <button type="button" class="filter-chip" [attr.aria-pressed]="filter() === f[0]" (click)="filter.set(f[0])">{{ f[1] }} · {{ counts()[f[0]] }}</button>
              }
            </div>
          }
        </div>
      </div>
      @if (catalog()) {
        @switch (cat.status()) {
          @case ('loading') {
            <p class="muted skills-body">Loading…</p>
          }
          @case ('error') {
            <div class="skills-body"><div deskEmptyState title="Couldn't load the catalog" [body]="cat.error()"></div></div>
          }
          @default {
            <div class="skills-body"><div deskCatalogView [items]="cat.items()" [layout]="layout.layout()" [projectNames]="projectNames()" (review)="openReview($event)"></div></div>
          }
        }
      } @else {
        @switch (data.status()) {
          @case ('loading') {
            <p class="muted skills-body">Loading…</p>
          }
          @case ('error') {
            <div deskEmptyState title="Couldn't load skills" [body]="data.error()"></div>
          }
          @default {
            @if (!data.nodes().length) {
              <div class="skills-body">
                @if (builtins.items().length) {
                  <section deskBuiltinGroup [items]="builtins.items()" [selected]="skill() ?? null" (selectSkill)="toggle($event)" (changed)="refreshBuiltins()"></section>
                }
                <div deskEmptyState title="No skills of your own yet" [body]="emptyBody"></div>
              </div>
            } @else if (view() === 'map') {
              <div class="builtin-strip">
                @if (builtins.items().length) {
                  <section deskBuiltinGroup [items]="builtins.items()" [selected]="skill() ?? null" collapsible (selectSkill)="toggle($event)" (changed)="refreshBuiltins()"></section>
                }
              </div>
              <div class="skills-body map">
                <div deskSkillsMapView [nodes]="shown()" [projects]="projects()" [catalogKeys]="catalogKeys()" [selected]="skill() ?? null" (selectSkill)="toggle($event)"></div>
              </div>
            } @else {
              <div class="skills-body">
                @if (builtins.items().length) {
                  <section deskBuiltinGroup [items]="builtins.items()" [selected]="skill() ?? null" (selectSkill)="toggle($event)" (changed)="refreshBuiltins()"></section>
                }
                <div deskSkillList [nodes]="shown()" [projectNames]="projectNames()" [catalogKeys]="catalogKeys()" [selected]="skill() ?? null" (selectSkill)="toggle($event)"></div>
              </div>
            }
          }
        }
      }
      <div class="skills-foot">
        @if (!catalog() && view() === 'map') {
          <div class="skills-legend" aria-hidden="true">
            <span><span class="lg-dot global"></span>Global skill</span>
            <span><span class="lg-dot project"></span>Project skill</span>
            <span><span class="lg-dot shadowed"></span>Shadowed</span>
            <span><span class="lg-line run"></span>Used by a running thread</span>
            <span><span class="lg-line wait"></span>Waiting</span>
            <span>Size = how often it's used</span>
          </div>
        } @else {
          <span></span>
        }
        <span class="grow"></span>
        <button deskButton (click)="startImport()">Import from ~/.claude/skills</button>
        <button deskButton (click)="editor.set({})">New skill</button>
        <button deskButton variant="primary" (click)="ask.set({})">+ Ask Desk for a new skill</button>
      </div>
    </div>
    @if (ref(); as r) {
      <article
        deskSkillPanel
        [skill]="r"
        [node]="node()"
        [catalog]="panelCatalog()"
        [projectNames]="projectNames()"
        [threadTitles]="threadTitles()"
        [version]="panelVersion()"
        (edit)="editor.set({ skill: { ref: r, detail: $event } })"
        (askDesk)="askAbout(r)"
        (changed)="changed()"
        (close)="select(null)"
      ></article>
    }
    @if (builtin(); as b) {
      <article deskBuiltinPanel [item]="b" [projects]="projects()" (duplicated)="onDuplicated($event)" (changed)="changed()" (close)="select(null)"></article>
    }
    @if (reviewId(); as id) {
      <div deskReviewSheet [id]="id" [item]="reviewItem()" [projects]="projects()" (changed)="changed()" (close)="closeReview()"></div>
    }
    @if (editor(); as e) {
      <div deskSkillEditor [skill]="e.skill" [projects]="projects()" (close)="editor.set(null)" (saved)="onSaved($event)"></div>
    }
    @if (ask(); as a) {
      <div deskAskDesk [skillName]="a.name" [projects]="projects()" [defaultProjectId]="a.projectId" (close)="ask.set(null)"></div>
    }
    @if (importPath(); as path) {
      <div deskImportSheet [path]="path" [projects]="projects()" (close)="importPath.set(null)" (done)="onImported($event)"></div>
    }
  `,
})
export class SkillsScreen implements SkillsRefresh {
  /** The open skill's key (`global:<name>` or `project:<id>:<name>`), from `#/skills/<key>`. */
  readonly skill = input<string>();
  /** True on `#/skills/catalog`. */
  readonly catalog = input(false, { transform: booleanAttribute });
  /** The catalog entry under review, from `#/skills/catalog/<id>`. */
  readonly review = input<string>();

  private readonly bridge = inject(DeskBridge);
  private readonly global = inject(GlobalStore);
  private readonly routes = inject(RouteService);
  private readonly toasts = inject(ToastService);
  protected readonly data = injectSkills();
  protected readonly builtins = injectBuiltins();
  protected readonly cat = injectCatalog();
  protected readonly layout = injectCatalogLayout();
  protected readonly filters = FILTERS;
  protected readonly mapBlurb = 'Global skills sit in the middle; project skills live inside their project. Lines show the threads using a skill right now.';
  protected readonly catalogBlurb = 'Skills worth having, pinned to an exact version and checked. Review one, install it, and Desk sets up what its scripts need.';
  protected readonly emptyBody = 'Skills are instructions and scripts Desk and its threads reuse. Import one, write one, or ask Desk to build one.';

  protected readonly view = signal<View>(storedView());
  protected readonly filter = signal<Filter>('all');
  protected readonly editor = signal<{ skill?: { ref: SkillRef; detail: SkillDetail } } | null>(null);
  protected readonly ask = signal<{ name?: string; projectId?: string } | null>(null);
  protected readonly importPath = signal<string | null>(null);
  private readonly version = signal(0);
  /** Set once the screen is gone: a panel's late `changed()` then does nothing. */
  private gone = false;

  private readonly fromCatalog = computed(() => catalogIndex(this.cat.items()));
  protected readonly catalogKeys = computed(() => new Set(this.fromCatalog().keys()));
  protected readonly projects = computed(() => this.global.state().overview.map((p) => ({ id: p.project.id, name: p.project.name, tone: projectTone(p) })));
  protected readonly projectNames = computed(() => new Map(this.projects().map((p) => [p.id, p.name])));
  protected readonly threadTitles = computed(() => new Map(this.global.state().overview.flatMap((p) => p.threads.map((t) => [t.id, t.title ?? 'Thread'] as const))));
  protected readonly counts = computed(() => {
    const nodes = this.data.nodes();
    return { all: nodes.length, used: nodes.filter((n) => n.usedBy.length).length, shadowed: nodes.filter((n) => n.shadows || n.shadowedIn.length).length };
  });
  protected readonly shown = computed(() => {
    const f = this.filter();
    return this.data.nodes().filter((n) => f === 'all' || (f === 'used' ? n.usedBy.length > 0 : n.shadows || n.shadowedIn.length > 0));
  });
  protected readonly ref = computed(() => {
    const key = this.skill();
    return !this.catalog() && key ? parseSkillKey(key) : null;
  });
  /** The open built-in skill, from a `builtin:<name>` key (React's `builtinName` and `builtin`). */
  protected readonly builtin = computed(() => {
    const key = this.skill();
    const name = !this.catalog() && key ? parseBuiltinKey(key) : null;
    return name ? this.builtins.items().find((b) => b.name === name) : undefined;
  });
  protected readonly node = computed(() => {
    const key = this.skill();
    return key ? this.data.nodes().find((n) => n.key === key) : undefined;
  });
  protected readonly panelCatalog = computed(() => {
    const key = this.skill();
    return key ? this.fromCatalog().get(key) : undefined;
  });
  protected readonly panelVersion = computed(() => this.version() + (this.node()?.version ?? 0));
  protected readonly reviewId = computed(() => (this.catalog() ? (this.review() ?? null) : null));
  protected readonly reviewItem = computed(() => {
    const id = this.reviewId();
    return id ? this.cat.items().find((i) => i.id === id) : undefined;
  });

  protected showView(next: View): void {
    this.view.set(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // A convenience only.
    }
    if (this.catalog()) this.routes.navigate({ name: 'skills' });
  }

  protected showCatalog(): void {
    if (!this.catalog()) this.routes.navigate({ name: 'catalog' });
  }

  /** The selection lives in the route, replaced rather than pushed. */
  protected select(key: string | null): void {
    this.routes.replace(key ? { name: 'skills', skill: key } : { name: 'skills' });
  }

  protected toggle(key: string): void {
    this.select(key === this.skill() ? null : key);
  }

  protected openReview(id: string): void {
    this.routes.replace({ name: 'catalog', review: id });
  }

  protected closeReview(): void {
    this.routes.replace({ name: 'catalog' });
  }

  constructor() {
    inject(DestroyRef).onDestroy(() => (this.gone = true));
  }

  /**
   * Something changed: list skills, the catalog and the built-ins again, and have the panel fetch its skill again. The
   * built-in panel's `changed` comes here too (React lists only the built-ins): a copy that lands after another built-in
   * opened emits only `changed`, and the new user skill must show without waiting for the next focus or poll. A panel
   * that closed while its call ran calls this directly (`SkillsRefresh`); once the screen is gone it does nothing.
   */
  changed(): void {
    if (this.gone) return;
    this.version.update((v) => v + 1);
    void this.data.refresh();
    void this.cat.refresh();
    void this.builtins.refresh();
  }

  protected refreshBuiltins(): void {
    void this.builtins.refresh();
  }

  /** A built-in was duplicated: list everything again and open the copy. */
  protected onDuplicated(r: SkillRef): void {
    this.changed();
    this.select(skillKey(r));
  }

  protected askAbout(r: SkillRef): void {
    const user = this.node()?.usedBy[0];
    this.ask.set({ name: r.name, ...(r.projectId ? { projectId: r.projectId } : user ? { projectId: user.projectId } : {}) });
  }

  protected async startImport(): Promise<void> {
    try {
      const path = await this.bridge.call('app.pickFolder', { purpose: 'skill-import' });
      if (path) this.importPath.set(path);
    } catch (err) {
      this.toasts.error(err);
    }
  }

  protected onSaved(saved: SkillRef): void {
    this.editor.set(null);
    this.toasts.toast({ tone: 'info', message: `Saved ${saved.name}.` });
    this.changed();
    this.select(skillKey(saved));
  }

  protected onImported(r: SkillRef): void {
    this.importPath.set(null);
    this.changed();
    this.select(skillKey(r));
  }
}
