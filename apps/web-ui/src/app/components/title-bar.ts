import { ChangeDetectionStrategy, Component, computed, effect, inject, input, untracked, ViewEncapsulation } from '@angular/core';
import { href, type Route } from '@desk/ui-core';
import { GlobalStore } from '../core/global.store';
import { LastProject } from '../core/last-project';
import { ProjectSwitcher } from './project-switcher';

function daemonLabel(status: string, proxy: string): { label: string; tone: '' | 'ok' | 'warn' | 'bad' } {
  switch (status) {
    case 'live':
      return { label: `deskd · proxy ${proxy}`, tone: proxy === 'up' ? 'ok' : 'warn' };
    case 'reconnecting':
      return { label: 'reconnecting…', tone: 'warn' };
    case 'offline':
      return { label: 'deskd not running', tone: 'bad' };
    case 'mismatch':
      return { label: 'deskd needs an update', tone: 'bad' };
    default:
      return { label: 'connecting…', tone: '' };
  }
}

/** Places nav (Map · project · ⌘P switcher · Skills · System), daemon status, and the attention pill. */
@Component({
  selector: 'header[deskTitleBar]',
  imports: [ProjectSwitcher],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'titlebar' },
  template: `
    <nav aria-label="Places" class="places">
      <a [href]="hrefs.map" [attr.aria-current]="route().name === 'map' ? 'page' : null">Map</a>
      @if (project(); as p) {
        <a [href]="projectHref(p.project.id)" [attr.aria-current]="projectId() ? 'page' : null" class="place-project">{{ p.project.name }}</a>
      }
      <div deskProjectSwitcher [currentId]="project()?.project.id ?? null"></div>
      <a [href]="hrefs.skills" [attr.aria-current]="route().name === 'skills' || route().name === 'catalog' ? 'page' : null">Skills</a>
      <a [href]="hrefs.system" [attr.aria-current]="route().name === 'system' ? 'page' : null">System</a>
    </nav>
    <span class="spacer"></span>
    <span class="daemon"><span class="dot" [class]="daemon().tone" aria-hidden="true"></span>{{ daemon().label }}</span>
    <a [href]="hrefs.attention" class="pill" [class.needs]="count() > 0" [class.clear]="count() === 0" [attr.aria-current]="route().name === 'attention' ? 'page' : null">{{ count() ? count() + ' need you' : 'All clear' }}</a>
  `,
})
export class TitleBar {
  readonly route = input.required<Route>();
  private readonly global = inject(GlobalStore).state;
  private readonly last = inject(LastProject);
  protected readonly hrefs = { map: href({ name: 'map' }), skills: href({ name: 'skills' }), system: href({ name: 'system' }), attention: href({ name: 'attention' }) };
  protected readonly projectId = computed(() => {
    const r = this.route();
    return r.name === 'project' ? r.id : null;
  });
  /** Inside a project, that project; elsewhere, the one the user was last in (while it is still open). */
  protected readonly project = computed(() => {
    const id = this.projectId() ?? this.last.id();
    return id ? this.global().overview.find((p) => p.project.id === id) : undefined;
  });
  protected readonly count = computed(() => this.global().attention.length);
  protected readonly daemon = computed(() => daemonLabel(this.global().connection.status, this.global().system.proxy));

  constructor() {
    effect(() => {
      const id = this.projectId();
      if (id) untracked(() => this.last.remember(id));
    });
  }

  protected projectHref(id: string): string {
    return href({ name: 'project', id, tab: 'conversation' });
  }
}
