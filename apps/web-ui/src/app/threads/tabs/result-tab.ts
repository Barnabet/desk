import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, input } from '@angular/core';
import type { ThreadView } from '@desk/client';
import { href } from '@desk/ui-core';
import { EmptyState } from '../../components/empty-state';
import { SafeMarkdown } from '../../components/safe-markdown';

/** The thread's report and its artifacts, which open in the Library. */
@Component({
  selector: 'div[deskResultTab]',
  imports: [EmptyState, SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (thread().result_summary; as summary) {
      <div class="tab-body result-tab">
        <div deskSafeMarkdown [text]="summary"></div>
        @if (artifacts().length) {
          <h3>Artifacts</h3>
          <div class="report-results">
            @for (a of artifacts(); track a) {
              <a class="file-chip" [href]="libraryHref(a)">{{ a }}</a>
            }
          </div>
        }
      </div>
    } @else {
      <div deskEmptyState title="No result yet" body="The thread reports here when it finishes. Desk reviews it and may send it back."></div>
    }
  `,
})
export class ResultTab {
  readonly projectId = input.required<string>();
  readonly thread = input.required<ThreadView>();
  protected readonly artifacts = computed(() => this.thread().result_artifacts ?? []);

  protected libraryHref(file: string): string {
    return href({ name: 'project', id: this.projectId(), tab: 'library', file });
  }
}
