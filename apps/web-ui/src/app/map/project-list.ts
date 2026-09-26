import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input } from '@angular/core';
import type { ProjectSummary } from '@desk/protocol';
import { href } from '@desk/ui-core';
import { Unread } from '../core/unread';
import { projectSummaryLine } from './project-summary';

/** The map's list alternative (ProjectList.tsx): one card per project, linking to its conversation. */
@Component({
  selector: 'div[deskProjectList]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'project-list' },
  template: `
    @for (c of cards(); track c.p.project.id) {
      <a class="card project-card" [href]="c.href">
        <h2>{{ c.p.project.name }}@if (unread.isUnread(c.p)) {<span class="unread-dot" aria-label="unread"></span>}</h2>
        @if (c.p.project.goal) {
          <p class="subtitle">{{ c.p.project.goal }}</p>
        }
        <span class="muted">{{ c.line }}</span>
        @if (c.p.latest_report; as report) {
          <span>{{ report.headline }}</span>
        }
        @if (c.p.attention_count) {
          <span class="needs-count">{{ c.p.attention_count }} need you</span>
        }
      </a>
    }
  `,
})
export class ProjectList {
  readonly projects = input.required<ProjectSummary[]>();

  protected readonly unread = inject(Unread);
  protected readonly cards = computed(() =>
    this.projects().map((p) => ({ p, href: href({ name: 'project', id: p.project.id, tab: 'conversation' }), line: projectSummaryLine(p) })),
  );
}
