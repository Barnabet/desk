import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, signal, type OnInit } from '@angular/core';
import { activityOf, layoutMap, plural } from '@desk/ui-core';
import { Button } from '../components/button';
import { EmptyState } from '../components/empty-state';
import { Sheet } from '../components/sheet';
import { GlobalStore } from '../core/global.store';
import { NowService } from '../core/now.service';
import { RouteService } from '../core/route.service';
import { ProjectForm } from '../screens/project-form';
import { DEFAULT_CANVAS_SIZE, MapCanvas, type CanvasSize } from './map-canvas';
import { OrbitMap } from './orbit-map';
import { ProjectList } from './project-list';
import { TerritoryInspector } from './territory-inspector';

type MapView = 'map' | 'list';

/** The Map/List choice, remembered per browser under the desktop's key (each origin has its own storage). */
const VIEW_KEY = 'desk.mapView';
const readView = (): MapView => {
  try {
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'map';
  } catch {
    return 'map';
  }
};

/**
 * The map screen (MapScreen.tsx): the heading, the counts and the Map/List switch; then the orbit map on its canvas
 * with the legend and the selected project's territory, or the list of projects; the empty state without projects;
 * and the new-project sheet.
 */
@Component({
  selector: 'div[deskMapScreen]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [Button, EmptyState, MapCanvas, OrbitMap, ProjectForm, ProjectList, Sheet, TerritoryInspector],
  host: { class: 'map-screen' },
  template: `
    <div class="map-head">
      <h1 class="title">Projects</h1>
      <p class="subtitle">{{ summary() }}</p>
      <div class="segmented" role="group" aria-label="View">
        <button type="button" [attr.aria-pressed]="view() === 'map'" (click)="choose('map')">Map</button>
        <button type="button" [attr.aria-pressed]="view() === 'list'" (click)="choose('list')">List</button>
      </div>
    </div>
    @if (!overview().length) {
      <div class="page">
        <div deskEmptyState title="No projects yet" [body]="'A project is a goal Desk works toward with its own threads, library and memory.'">
          <button deskButton (click)="creating.set(true)">Create a project</button>
        </div>
      </div>
    } @else if (view() === 'map') {
      <div deskMapCanvas label="Projects map" (resized)="canvasSize.set($event)">
        <div
          deskOrbitMap
          [layout]="orbit().layout"
          [projects]="overview()"
          [attention]="attention()"
          [selected]="selectedId()"
          (pick)="picked.set($event)"
          [width]="orbit().width"
          [height]="orbit().height"
          [now]="now()"
          [sunLabel]="sunLabel()"
          [sunAria]="sunAria()"
        ></div>
      </div>
      <div class="map-legend">
        <span><span class="legend-disc legend-running"></span>Running</span>
        <span><span class="legend-disc legend-waiting"></span>Waiting</span>
        <span><span class="legend-disc legend-idle"></span>Idle</span>
        <span><span class="legend-dot legend-dot-running"></span>Thread running</span>
        <span><span class="legend-dot legend-dot-waiting"></span>Waiting</span>
        <span><span class="legend-dot legend-dot-needs"></span>Needs you</span>
        <span class="muted">Disc size = activity</span>
      </div>
      @if (selected(); as p) {
        <article deskTerritoryInspector [p]="p" [items]="selectedItems()" [now]="now()"></article>
      }
    } @else {
      <div class="page">
        <div deskProjectList [projects]="overview()"></div>
      </div>
    }
    <button type="button" class="new-project-btn" (click)="creating.set(true)"><span aria-hidden="true">+</span> New project <span class="muted">· brief Desk in a sentence</span></button>
    @if (creating()) {
      <div deskSheet title="New project" (close)="close()">
        <form deskProjectForm [cancelable]="true" (cancelled)="close()" (created)="opened($event)"></form>
      </div>
    }
  `,
})
export class MapScreen implements OnInit {
  /** `#/map?new=1`: the new-project sheet is open on arrival. */
  readonly newProject = input(false);

  private readonly global = inject(GlobalStore);
  private readonly routes = inject(RouteService);
  protected readonly overview = computed(() => this.global.state().overview);
  /** Threads running across every project (the subtitle and the sun's second line). */
  private readonly running = computed(() => this.overview().reduce((n, p) => n + p.threads.filter((t) => t.status === 'running').length, 0));
  protected readonly summary = computed(() => {
    const { overview, attention } = this.global.state();
    const busy = overview.filter((p) => p.threads.some((t) => t.status === 'running')).length;
    const waiting = attention.length === 1 ? '1 thing is' : `${attention.length} things are`;
    return `${plural(this.running(), 'thread')} running in ${plural(busy, 'project')}. ${waiting} waiting on you.`;
  });
  protected readonly creating = signal(false);

  protected readonly attention = computed(() => this.global.state().attention);
  protected readonly now = inject(NowService).now;
  protected readonly view = signal<MapView>(readView());
  /** The project whose Desk disc was pressed. */
  protected readonly picked = signal<string | null>(null);
  /** The pressed project, else the first that needs the user, else the first. */
  protected readonly selected = computed(() => {
    const overview = this.overview();
    const id = this.picked();
    return overview.find((p) => p.project.id === id) ?? overview.find((p) => p.attention_count > 0) ?? overview[0];
  });
  protected readonly selectedId = computed(() => this.selected()?.project.id ?? null);
  protected readonly selectedItems = computed(() => {
    const id = this.selectedId();
    return this.attention().filter((i) => i.project_id === id);
  });
  /** The canvas's size as MapCanvas measures it (React passed it to MapCanvas's render prop). */
  protected readonly canvasSize = signal<CanvasSize>(DEFAULT_CANVAS_SIZE);
  private readonly mapProjects = computed(() => this.overview().map((p) => ({ id: p.project.id, activity: activityOf(p), threads: p.threads })));
  /** The orbit layout; a canvas wider than 900px keeps 380px on the right for the territory inspector. */
  protected readonly orbit = computed(() => {
    const size = this.canvasSize();
    const width = size.width > 900 ? size.width - 380 : size.width;
    return { width, height: size.height, layout: layoutMap(this.mapProjects(), width, size.height) };
  });
  protected readonly sunLabel = computed((): [string, string] => {
    const { health, connection, system } = this.global.state();
    const status = connection.status === 'live' ? 'running' : connection.status;
    return [`${health?.version ?? ''} · ${status}`, `proxy ${system.proxy} · ${plural(this.running(), 'thread')} running`];
  });
  protected readonly sunAria = computed(() => {
    const { connection, system } = this.global.state();
    return `deskd, ${connection.status === 'live' ? 'running' : connection.status}, model proxy ${system.proxy}`;
  });

  protected choose(v: MapView): void {
    this.view.set(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      // A convenience only.
    }
  }

  ngOnInit(): void {
    // Read once, as React's useState(newProject): a later ?new=1 on the same screen does not reopen it.
    this.creating.set(this.newProject());
  }

  protected close(): void {
    this.creating.set(false);
    if (this.newProject()) this.routes.navigate({ name: 'map' });
  }

  protected opened(id: string): void {
    this.creating.set(false);
    this.routes.navigate({ name: 'project', id, tab: 'conversation' });
  }
}
