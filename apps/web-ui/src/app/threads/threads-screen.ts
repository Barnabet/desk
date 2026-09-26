import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input } from '@angular/core';
import { href } from '@desk/ui-core';
import { EmptyState } from '../components/empty-state';
import { NowService } from '../core/now.service';
import { injectSession } from '../core/session.service';
import { ThreadDetail } from './thread-detail';
import { ThreadRoster } from './thread-roster';

type View = 'loading' | 'failed' | 'roster' | 'missing' | 'detail';

/**
 * The roster, or one thread; `at` opens the thread at the stop that holds that event (a message's id). The host is the
 * page itself while loading or when something is missing, and adds no box around the roster or the detail, whose own
 * roots (`div.page.roster`, `div.thread-detail`) are the desktop's.
 */
@Component({
  selector: 'div[deskThreadsScreen]',
  imports: [EmptyState, ThreadDetail, ThreadRoster],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { '[class]': 'hostClass()', '[style.display]': "view() === 'roster' || view() === 'detail' ? 'contents' : null" },
  template: `
    @switch (view()) {
      @case ('loading') {Loading…}
      @case ('failed') {
        <div deskEmptyState [title]="failedTitle()" [body]="s().error"><a href="#/map">Back to the map</a></div>
      }
      @case ('roster') {
        <div deskThreadRoster [project]="s().project!" [messages]="s().messages" [now]="now()"></div>
      }
      @case ('missing') {
        <div deskEmptyState title="This thread isn't here" body="It may belong to another project."><a [href]="allThreads()">All threads</a></div>
      }
      @case ('detail') {
        @if (thread(); as t) {
          <div deskThreadDetail [s]="s()" [thread]="t" [at]="at()"></div>
        }
      }
    }
  `,
})
export class ThreadsScreen {
  readonly projectId = input.required<string>();
  readonly threadId = input<string>();
  readonly at = input<number>();
  protected readonly s = injectSession(this.projectId);
  protected readonly now = inject(NowService).now;
  protected readonly thread = computed(() => {
    const id = this.threadId();
    return id ? (this.s().project?.threads.find((t) => t.id === id) ?? null) : null;
  });
  protected readonly view = computed<View>(() => {
    const s = this.s();
    if (s.status === 'loading') return 'loading';
    if (s.status !== 'ready' || !s.project) return 'failed';
    if (!this.threadId()) return 'roster';
    return this.thread() ? 'detail' : 'missing';
  });
  protected readonly hostClass = computed(() => {
    const v = this.view();
    return v === 'loading' ? 'page muted' : v === 'failed' || v === 'missing' ? 'page' : '';
  });
  protected readonly failedTitle = computed(() => (this.s().status === 'missing' ? "This project isn't here" : "Couldn't load this project"));
  protected readonly allThreads = computed(() => href({ name: 'project', id: this.projectId(), tab: 'threads' }));
}
