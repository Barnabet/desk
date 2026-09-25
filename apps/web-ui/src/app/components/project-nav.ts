import { ChangeDetectionStrategy, Component, input, ViewEncapsulation } from '@angular/core';
import { href, PROJECT_TABS, type ProjectTab } from '@desk/ui-core';

const LABEL: Record<ProjectTab, string> = { conversation: 'Conversation', threads: 'Threads', library: 'Library', memory: 'Memory', settings: 'Settings' };

/** A project's tabs, under the title bar. */
@Component({
  selector: 'nav[deskProjectNav]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'subnav', 'aria-label': 'Project' },
  template: `
    @for (t of tabs; track t) {
      <a [href]="hrefOf(t)" [attr.aria-current]="t === tab() ? 'page' : null">{{ label[t] }}</a>
    }
  `,
})
export class ProjectNav {
  readonly projectId = input.required<string>();
  readonly tab = input.required<ProjectTab>();
  protected readonly tabs = PROJECT_TABS;
  protected readonly label = LABEL;

  protected hrefOf(t: ProjectTab): string {
    return href({ name: 'project', id: this.projectId(), tab: t });
  }
}
