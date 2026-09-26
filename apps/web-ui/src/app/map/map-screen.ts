import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, signal, type OnInit } from '@angular/core';
import { plural } from '@desk/ui-core';
import { Button } from '../components/button';
import { EmptyState } from '../components/empty-state';
import { Sheet } from '../components/sheet';
import { GlobalStore } from '../core/global.store';
import { RouteService } from '../core/route.service';
import { ProjectForm } from '../screens/project-form';

/**
 * The map screen (MapScreen.tsx). W0 is its shell: the heading and counts, the empty state, and the new-project
 * sheet. W1 adds the orbit map, the Map/List switch, the legend and the territory inspector.
 */
@Component({
  selector: 'div[deskMapScreen]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [Button, EmptyState, ProjectForm, Sheet],
  host: { class: 'map-screen' },
  template: `
    <div class="map-head">
      <h1 class="title">Projects</h1>
      <p class="subtitle">{{ summary() }}</p>
    </div>
    @if (!overview().length) {
      <div class="page">
        <div deskEmptyState title="No projects yet" [body]="'A project is a goal Desk works toward with its own threads, library and memory.'">
          <button deskButton (click)="creating.set(true)">Create a project</button>
        </div>
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
  protected readonly summary = computed(() => {
    const { overview, attention } = this.global.state();
    const running = overview.reduce((n, p) => n + p.threads.filter((t) => t.status === 'running').length, 0);
    const busy = overview.filter((p) => p.threads.some((t) => t.status === 'running')).length;
    const waiting = attention.length === 1 ? '1 thing is' : `${attention.length} things are`;
    return `${plural(running, 'thread')} running in ${plural(busy, 'project')}. ${waiting} waiting on you.`;
  });
  protected readonly creating = signal(false);

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
