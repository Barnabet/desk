import { ChangeDetectionStrategy, Component, computed, inject, input, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDetail } from '@desk/protocol';
import { dayTime, href, runStatusText, runTook, skippedText, triggerText } from '@desk/ui-core';
import { Button } from '../../components/button';
import { EmptyState } from '../../components/empty-state';
import { NowService } from '../../core/now.service';
import type { SessionState } from '../../core/session.service';
import { injectRuns } from '../data';

/** The Runs tab (spec §8.3): newest first, with skipped schedule times as lines of their own. */
@Component({
  selector: 'div[deskRunsList]',
  imports: [Button, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (live.status() === 'loading' && !live.value()) {
      <p class="muted auto-loading">Loading…</p>
    } @else if (!rows().length) {
      <div deskEmptyState title="No runs yet" body="Run now or Test starts one; schedules start them while it is on."></div>
    } @else {
      <div class="auto-runs">
        <table class="auto-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Started</th>
              <th>Trigger</th>
              <th>Status</th>
              <th>Took</th>
              <th>Summary</th>
            </tr>
          </thead>
          <tbody>
            @for (e of rows(); track e.key) {
              @if (e.run; as r) {
                <tr>
                  <td><a class="mono" [href]="r.href">{{ '#' + r.number }}</a></td>
                  <td>{{ r.started }}</td>
                  <td>{{ r.trigger }}</td>
                  <td><span class="auto-last" [class]="'tone-' + r.tone"><span class="dot" aria-hidden="true"></span>{{ r.status }}</span></td>
                  <td>{{ r.took }}</td>
                  <td class="auto-summary">{{ r.summary }}</td>
                </tr>
              } @else {
                <tr class="auto-skipped">
                  <td></td>
                  <td colspan="5" class="muted small">{{ e.skipped }}</td>
                </tr>
              }
            }
          </tbody>
        </table>
        @if (rows().length >= limit()) {
          <div><button deskButton size="sm" (click)="limit.set(limit() + 50)">Show older runs</button></div>
        }
      </div>
    }
  `,
})
export class RunsList {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly detail = input.required<AutomationDetail>();
  private readonly now = inject(NowService).now;
  protected readonly limit = signal(50);
  protected readonly live = injectRuns(() => this.s(), () => this.detail().id, () => this.limit());
  protected readonly rows = computed(() => {
    const now = this.now();
    return (this.live.value() ?? []).map((e) => {
      if (e.kind === 'skipped') return { key: `skip-${e.due_at}-${e.trigger_index}`, skipped: `${dayTime(e.due_at, now)} · ${skippedText(e)}`, run: null };
      const r = e.run;
      const st = runStatusText(r);
      return {
        key: r.id,
        skipped: null,
        run: {
          href: href({ name: 'project', id: this.projectId(), tab: 'automations', automationId: this.detail().id, view: 'runs', runId: r.id }),
          number: r.number,
          started: dayTime(r.started_at, now),
          trigger: triggerText(r),
          tone: st.tone,
          status: st.text,
          took: runTook(r, now),
          summary: r.summary ?? r.reason ?? '',
        },
      };
    });
  });
}
