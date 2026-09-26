import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, effect, inject, input, signal, untracked } from '@angular/core';
import type { AttentionItem, OverviewThread, PlanItem, ProjectSummary } from '@desk/protocol';
import { ago, clock, href, waitsOnYou } from '@desk/ui-core';
import { DeskBridge } from '../core/desk-bridge';
import { projectSummaryLine, projectTone, type Tone } from './project-summary';

const BADGE: Record<Tone, string> = { running: 'chip-run', waiting: 'chip-wait', idle: 'chip-idle' };

function waypointTone(item: PlanItem, p: ProjectSummary): string {
  if (item.status === 'done') return 'done';
  if (item.status === 'dropped') return 'dropped';
  if (item.status === 'todo') return 'todo';
  return p.threads.some((t) => item.thread_ids.includes(t.id) && t.status === 'waiting') ? 'wait' : 'run';
}

function threadLine(t: OverviewThread, waitingOnYou: boolean, now: number): string {
  switch (t.status) {
    case 'running':
      return t.review_round ? `revision ${t.review_round} · ${ago(t.created_at, now)}` : `running · ${ago(t.created_at, now)}`;
    case 'waiting':
      return waitingOnYou ? 'waiting on you' : 'waiting';
    case 'queued':
      return /restart/i.test(t.reason ?? '') ? 'will resume' : 'queued';
    default:
      return t.status;
  }
}

type PlanKey = { id: string; done: number; total: number };

/** The TERRITORY card (TerritoryInspector.tsx): latest report, plan waypoints, threads, needs-you, and the ways in. */
@Component({
  selector: 'article[deskTerritoryInspector]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'card territory', 'aria-label': 'Territory', role: 'region', 'aria-live': 'polite' },
  template: `
    <div class="territory-head">
      <span class="eyebrow">Territory</span>
      <span [class]="'chip ' + badge()">{{ summary() }}</span>
    </div>
    <h2 class="territory-name">{{ p().project.name }}</h2>
    @if (p().latest_report; as report) {
      <div class="territory-quote">
        <p>“{{ report.headline }}”</p>
        <span class="muted">Desk · report {{ reportClock() }}</span>
      </div>
    } @else {
      <p class="muted">{{ p().project.goal || 'No report yet.' }}</p>
    }
    <div class="territory-block">
      <div class="territory-row">
        <strong>Plan</strong>
        <span class="muted">{{ progress() }}</span>
      </div>
      @if (waypoints().length) {
        <div class="waypoints" aria-hidden="true">
          <span class="waypoints-rule"></span>
          @for (w of waypoints(); track w.id) {
            <span [class]="'waypoint waypoint-' + w.tone" [attr.title]="w.title"></span>
          }
        </div>
      }
    </div>
    @if (threads().length) {
      <div class="territory-block">
        <strong>Threads</strong>
        @for (t of threads(); track t.id) {
          <a class="territory-thread" [href]="t.href">
            <span [class]="'status-dot status-dot-' + t.status" aria-hidden="true"></span>
            <span class="grow">{{ t.title }}</span>
            <span class="territory-thread-status" [class]="'status-text-' + t.status">{{ t.line }}</span>
          </a>
        }
      </div>
    }
    @if (needs().length) {
      <div class="needs-box">
        <strong>Needs you · {{ needs().length }}</strong>
        @for (n of needs(); track n.id) {
          <a [href]="n.href">{{ n.title }}</a>
        }
      </div>
    }
    <div class="actions">
      <a class="btn btn-primary grow" [href]="conversationHref()">Open conversation</a>
      <a class="btn btn-secondary grow" [href]="attentionHref()">Attention</a>
    </div>
  `,
})
export class TerritoryInspector {
  readonly p = input.required<ProjectSummary>();
  /** This project's attention items. */
  readonly items = input.required<AttentionItem[]>();
  readonly now = input.required<number>();

  private readonly bridge = inject(DeskBridge);
  /** The plan's items: null while loading, [] when there is none or it could not be read. */
  private readonly plan = signal<PlanItem[] | null>(null);
  /** What the plan request depends on (React's effect deps): a new overview push alone does not refetch. */
  private readonly planKey = computed<PlanKey>(
    () => {
      const p = this.p();
      return { id: p.project.id, done: p.plan_progress.done, total: p.plan_progress.total };
    },
    { equal: (a, b) => a.id === b.id && a.done === b.done && a.total === b.total },
  );

  protected readonly badge = computed(() => BADGE[projectTone(this.p())]);
  protected readonly summary = computed(() => projectSummaryLine(this.p(), this.items()));
  protected readonly reportClock = computed(() => {
    const report = this.p().latest_report;
    return report ? clock(report.ts) : '';
  });
  protected readonly progress = computed(() => {
    const plan = this.plan();
    const p = this.p();
    const done = plan?.filter((i) => i.status === 'done').length ?? p.plan_progress.done;
    const total = plan?.length ?? p.plan_progress.total;
    return total ? (done === total ? `all ${total} done` : `${done} of ${total} done`) : 'no plan yet';
  });
  protected readonly waypoints = computed(() => {
    const p = this.p();
    return (this.plan() ?? []).map((i) => ({ id: i.id, title: i.title, tone: waypointTone(i, p) }));
  });
  protected readonly threads = computed(() => {
    const p = this.p();
    const items = this.items();
    const now = this.now();
    return p.threads.map((t) => ({
      id: t.id,
      status: t.status,
      title: t.title ?? 'Thread',
      href: href({ name: 'project', id: p.project.id, tab: 'threads', threadId: t.id }),
      line: threadLine(t, items.some((i) => i.ref.thread_id === t.id && waitsOnYou(i)), now),
    }));
  });
  protected readonly needs = computed(() => this.items().map((i) => ({ id: i.id, title: i.title, href: href({ name: 'attention', item: i.id }) })));
  protected readonly conversationHref = computed(() => href({ name: 'project', id: this.p().project.id, tab: 'conversation' }));
  protected readonly attentionHref = computed(() => {
    const first = this.items()[0];
    return href(first ? { name: 'attention', item: first.id } : { name: 'attention' });
  });

  constructor() {
    effect((onCleanup) => {
      const { id } = this.planKey();
      let live = true;
      this.plan.set(null);
      untracked(() => this.bridge.call('projects.plan', { id })).then(
        (row) => {
          if (live) this.plan.set(row?.items ?? []);
        },
        () => {
          if (live) this.plan.set([]);
        },
      );
      onCleanup(() => {
        live = false;
      });
    });
  }
}
