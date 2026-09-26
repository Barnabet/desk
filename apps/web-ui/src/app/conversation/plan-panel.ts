import { ChangeDetectionStrategy, Component, ViewEncapsulation, booleanAttribute, computed, input } from '@angular/core';
import type { ProjectState, ThreadView } from '@desk/client';
import type { AttentionItem, PlanItem } from '@desk/protocol';
import { href, waitsOnYou } from '@desk/ui-core';
import { StatusChip } from '../components/status-chip';

const STATUS: Record<PlanItem['status'], string> = { todo: 'To do', in_progress: 'In progress', done: 'Done', dropped: 'Dropped' };

/** A plan item with what its stop shows: the tone, the threads it names, and whether one of them waits on the user. */
type Stop = { item: PlanItem; tone: string; linked: ThreadView[]; onYou: boolean };

/** The plan as a route of waypoints, the merge note, and Desk's card. */
@Component({
  selector: 'aside[deskPlanPanel]',
  imports: [StatusChip],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card plan-panel', 'aria-label': 'Plan and Desk' },
  template: `
    @let p = project();
    @let s = p.project.settings;
    <div class="plan-head">
      <h2>Plan</h2>
      <span class="chip chip-idle">{{ p.plan.length ? done() + ' of ' + p.plan.length + ' done' : 'No plan yet' }}</span>
    </div>
    @if (p.plan.length) {
      <ol class="plan-route">
        @for (st of stops(); track st.item.id) {
          <li class="plan-stop" [class]="'plan-' + st.tone">
            <span class="plan-dot" aria-hidden="true"></span>
            <div class="plan-text">
              <span class="plan-title">{{ st.item.title }}</span>
              <span class="plan-sub" [class]="'plan-sub-' + st.tone">{{ status[st.item.status] }}@for (t of st.linked; track t.id) {<span> · <a [href]="threadHref(t.id)">{{ t.title ?? 'Thread' }}</a>{{ revision(t) }}</span>}{{ st.onYou ? ' · waiting on you' : '' }}{{ st.item.status === 'todo' && !st.linked.length ? ' · Desk, once the threads report' : '' }}</span>
              @if (st.item.notes) {
                <span class="muted small">{{ st.item.notes }}</span>
              }
            </div>
          </li>
        }
      </ol>
    } @else {
      <p class="muted">Desk writes the plan once it understands the brief.</p>
    }
    @if (branches().length) {
      <div class="merge-note">
        <span>Desk never merges; you'll get a merge order.</span>
        @for (b of branches(); track b) {
          <span class="mono small">{{ b }}</span>
        }
      </div>
    }
    @if (p.desk; as desk) {
      <div class="desk-card">
        <div class="desk-card-head">
          <span class="desk-avatar" aria-hidden="true">Desk</span>
          <span class="grow"><strong>Desk</strong><span class="muted small"> coordinator</span></span>
          <span deskStatusChip [status]="desk.status" [reason]="desk.reason" [proxyDown]="proxyDown()"></span>
        </div>
        <dl>
          <div><dt>Model</dt><dd class="mono">{{ desk.model_override ?? desk.model }}</dd></div>
          <div><dt>Check-ins</dt><dd>{{ s.check_in }}</dd></div>
          <div><dt>Autonomy</dt><dd>{{ s.autonomy === 'dispatch-freely' ? 'dispatches freely' : 'asks before dispatching' }}</dd></div>
        </dl>
      </div>
    }
  `,
})
export class PlanPanel {
  readonly project = input.required<ProjectState>();
  readonly proxyDown = input(false, { transform: booleanAttribute });
  readonly attention = input.required<readonly AttentionItem[]>();
  protected readonly status = STATUS;
  protected readonly done = computed(() => this.project().plan.filter((i) => i.status === 'done').length);
  protected readonly branches = computed(() => this.project().threads.flatMap((t) => (t.git_branch && !t.archived_at ? [t.git_branch] : [])));
  protected readonly stops = computed((): Stop[] => {
    const p = this.project();
    const attention = this.attention();
    const threads = new Map(p.threads.map((t) => [t.id, t]));
    return p.plan.map((item) => {
      const linked = item.thread_ids.map((id) => threads.get(id)).filter((t) => t !== undefined);
      const waiting = linked.some((t) => t.status === 'waiting');
      // "waiting on you" only for a thread with an attention item that waits on the user, TerritoryInspector's check (design spec §8 item 4).
      const onYou = linked.some((t) => t.status === 'waiting' && attention.some((a) => a.ref.thread_id === t.id && waitsOnYou(a)));
      const tone = item.status === 'in_progress' ? (waiting ? 'wait' : 'run') : item.status;
      return { item, tone, linked, onYou };
    });
  });

  protected threadHref(threadId: string): string {
    return href({ name: 'project', id: this.project().project.id, tab: 'threads', threadId });
  }

  /** " · revision 1/2" while a thread that is not done is being revised. */
  protected revision(t: ThreadView): string {
    return t.review_round && t.status !== 'done' ? ` · revision ${t.review_round}/${this.project().project.settings.review_rounds}` : '';
  }
}
