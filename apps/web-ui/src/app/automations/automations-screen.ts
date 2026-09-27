import { ChangeDetectionStrategy, Component, computed, input, ViewEncapsulation } from '@angular/core';
import { href, type AutomationView } from '@desk/ui-core';
import { EmptyState } from '../components/empty-state';
import { injectSession, type SessionState } from '../core/session.service';
import { AutomationHeader, DraftHeader } from './automation-header';
import { AutomationList } from './automation-list';
import { injectAutomation } from './data';
import { DesignView } from './design/design-view';
import { GrantsView } from './grants/grants-view';
import { RunsList } from './runs/runs-list';
import { RunView } from './runs/run-view';
import { VersionsView } from './versions/versions-view';

/** The project's git sources, for an agent step's worktree. */
const gitSources = (s: SessionState) => (s.project?.sources ?? []).filter((x) => x.kind === 'git').map((x) => ({ id: x.id, label: x.label }));

/** One automation: its header, then Design, Runs (or one run), Versions or Grants. */
@Component({
  selector: 'div[deskOneAutomation]',
  imports: [AutomationHeader, DesignView, EmptyState, GrantsView, RunsList, RunView, VersionsView],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (live.status() === 'missing') {
      <div deskEmptyState title="This automation is gone" body="It was deleted."><a [href]="all()">All automations</a></div>
    } @else {
      @if (live.value(); as d) {
        <div class="automation">
          <header deskAutomationHeader [projectId]="projectId()" [detail]="d" [view]="view()" (detailChange)="live.replace($event)"></header>
          <div class="automation-body">
            @switch (view()) {
              @case ('runs') {
                @if (runId(); as rid) {
                  @for (k of [rid]; track k) {
                    <div deskRunView [projectId]="projectId()" [s]="s()" [detail]="d" [runId]="k"></div>
                  }
                } @else {
                  <div deskRunsList [projectId]="projectId()" [s]="s()" [detail]="d"></div>
                }
              }
              @case ('versions') {
                <div deskVersionsView [detail]="d" (detailChange)="live.replace($event)"></div>
              }
              @case ('grants') {
                <div deskGrantsView [projectId]="projectId()" [s]="s()" [detail]="d" (detailChange)="live.replace($event)"></div>
              }
              @default {
                @for (k of [d.id]; track k) {
                  <div deskDesignView [projectId]="projectId()" [sources]="sources()" [detail]="d" (detailChange)="live.replace($event)"></div>
                }
              }
            }
          </div>
        </div>
      } @else if (live.status() === 'error') {
        <div deskEmptyState title="Couldn't load this automation" [body]="live.error()"><a [href]="all()">All automations</a></div>
      } @else {
        <p class="muted auto-loading">Loading…</p>
      }
    }
  `,
})
export class OneAutomation {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly id = input.required<string>();
  readonly view = input.required<AutomationView>();
  readonly runId = input<string | undefined>(undefined);
  protected readonly live = injectAutomation(() => this.s(), () => this.id());
  protected readonly sources = computed(() => gitSources(this.s()));
  protected readonly all = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations' }));
}

/** A Blank automation before its first save (spec §8.1): only Design, on a local draft. */
@Component({
  selector: 'div[deskDraftAutomation]',
  imports: [DesignView, DraftHeader],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'automation' },
  template: `
    <header deskDraftHeader [projectId]="projectId()" [name]="name()"></header>
    <div class="automation-body">
      @for (k of [name()]; track k) {
        <div deskDesignView [projectId]="projectId()" [sources]="sources()" [draftName]="k"></div>
      }
    </div>
  `,
})
export class DraftAutomation {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly name = input.required<string>();
  protected readonly sources = computed(() => gitSources(this.s()));
}

/** The Automations tab (spec §8.1): the list, one automation (header, then Design, Runs, Versions or Grants), or a new draft. */
@Component({
  selector: 'div[deskAutomationsScreen]',
  imports: [AutomationList, DraftAutomation, EmptyState, OneAutomation],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @let draftName = draft();
    @let oneId = automationId();
    @if (s().status === 'missing') {
      <div deskEmptyState title="This project is gone"></div>
    } @else if (draftName) {
      <div deskDraftAutomation [projectId]="projectId()" [s]="s()" [name]="draftName"></div>
    } @else if (oneId) {
      <div deskOneAutomation [projectId]="projectId()" [s]="s()" [id]="oneId" [view]="view() ?? 'design'" [runId]="runId()"></div>
    } @else {
      <div deskAutomationList [projectId]="projectId()" [s]="s()"></div>
    }
  `,
})
export class AutomationsScreen {
  readonly projectId = input.required<string>();
  readonly automationId = input<string | undefined>(undefined);
  readonly view = input<AutomationView | undefined>(undefined);
  readonly runId = input<string | undefined>(undefined);
  readonly draft = input<string | undefined>(undefined);
  protected readonly s = injectSession(() => this.projectId());
}
