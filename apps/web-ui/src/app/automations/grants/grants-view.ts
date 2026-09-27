import { booleanAttribute, ChangeDetectionStrategy, Component, computed, effect, inject, input, linkedSignal, output, signal, untracked, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, AutomationDetail, AutomationVersionInfo, Grant } from '@desk/protocol';
import {
  dayTime,
  describeGrant,
  diffDefinitions,
  GRANT_MATCH_LABEL,
  GRANT_TOOLS,
  GRANT_VALUE_LABEL,
  grantDraft,
  grantFromDraft,
  grantKey,
  grantOrigins,
  grantOriginText,
  href,
  originText,
  replaceGrant,
  widenedGrants,
  type GrantDraft,
  type GrantMatchKind,
} from '@desk/ui-core';
import { Button } from '../../components/button';
import { EmptyState } from '../../components/empty-state';
import { Field } from '../../components/field';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';
import type { SessionState } from '../../core/session.service';
import { DiffView } from '../versions/diff-view';

const val = (e: Event): string => (e.target as HTMLInputElement).value;

/** A new grant, or an edit: action, tool, what it matches, checked by grantFromDraft before it saves. */
@Component({
  selector: 'form[deskGrantEditor]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-grant-form', '[attr.aria-label]': "initial() ? 'Edit grant' : 'New grant'", '(submit)': 'submit($event)' },
  template: `
    <div class="auto-inline">
      <div deskField id="grant-action" label="Action">
        <select id="grant-action" class="select" (change)="setAction(val($event))">
          <option value="allow" [selected]="draft().action === 'allow'">Allow</option>
          <option value="deny" [selected]="draft().action === 'deny'">Deny</option>
        </select>
      </div>
      <div deskField id="grant-tool" label="Tool">
        <input id="grant-tool" class="input mono" list="grant-tools" maxlength="60" [value]="draft().tool" (input)="set({ tool: val($event) })" />
      </div>
      <datalist id="grant-tools">
        @for (t of tools; track t) {
          <option [value]="t"></option>
        }
      </datalist>
      <div deskField id="grant-kind" label="Matches">
        <select id="grant-kind" class="select" (change)="setKind(val($event))">
          @for (k of kinds; track k.value) {
            <option [value]="k.value" [selected]="k.value === draft().kind">{{ k.label }}</option>
          }
        </select>
      </div>
    </div>
    @if (draft().kind !== 'any') {
      <div deskField id="grant-value" [label]="valueLabel()">
        <input id="grant-value" class="input mono" [value]="draft().value" (input)="set({ value: val($event) })" />
      </div>
    }
    @if (problem()) {
      <p class="field-error" role="alert">{{ problem() }}</p>
    }
    <div class="auto-inline">
      <button deskButton type="submit" variant="primary" [pending]="busy()">Save grant</button>
      <button deskButton (click)="cancel.emit()">Cancel</button>
    </div>
  `,
})
export class GrantEditor {
  readonly initial = input<Grant | undefined>(undefined);
  readonly busy = input(false, { transform: booleanAttribute });
  /** React's onSave: the checked grant. */
  readonly save = output<Grant>();
  readonly cancel = output<void>();
  protected readonly val = val;
  protected readonly tools = GRANT_TOOLS;
  protected readonly kinds = (Object.keys(GRANT_MATCH_LABEL) as GrantMatchKind[]).map((value) => ({ value, label: GRANT_MATCH_LABEL[value] }));
  protected readonly draft = linkedSignal<GrantDraft>(() => untracked(() => grantDraft(this.initial())));
  protected readonly problem = signal<string | null>(null);
  protected readonly valueLabel = computed(() => {
    const kind = this.draft().kind;
    return kind === 'any' ? '' : GRANT_VALUE_LABEL[kind];
  });

  protected set(patch: Partial<GrantDraft>): void {
    this.draft.update((x) => ({ ...x, ...patch }));
    this.problem.set(null);
  }

  protected setAction(v: string): void {
    this.set({ action: v as Grant['action'] });
  }

  protected setKind(v: string): void {
    this.set({ kind: v as GrantMatchKind });
  }

  protected submit(e: Event): void {
    e.preventDefault();
    const r = grantFromDraft(this.draft());
    if ('problem' in r) this.problem.set(r.problem);
    else this.save.emit(r.grant);
  }
}

/** Grants are suspended: the versions saved since they were set, what changed, and Keep grants. */
@Component({
  selector: 'section[deskSuspendedBanner]',
  imports: [Button, DiffView],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-banner', 'aria-label': 'Grants suspended' },
  template: `
    <p><b>Grants are suspended.</b>{{ text() }}</p>
    @if (saved().length) {
      <ul class="auto-plain">
        @for (v of saved(); track v.version) {
          <li>
            <b>{{ 'v' + v.version }}</b>&ngsp;<span class="muted small">{{ originText(v) + ' · ' + dayTime(v.created_at, now()) }}</span>
            @if (v.change_note) {
              <p>{{ v.change_note }}</p>
            }
          </li>
        }
      </ul>
    }
    @if (since() !== null) {
      <section [attr.aria-label]="'What changed since v' + since()">
        @if (diff(); as d) {
          <div deskDiffView [diff]="d" [labels]="labels()"></div>
        } @else {
          <p class="muted">Loading…</p>
        }
      </section>
    }
    <div><button deskButton variant="primary" [pending]="keeping()" (click)="keep()">Keep grants</button></div>
  `,
})
export class SuspendedBanner {
  readonly detail = input.required<AutomationDetail>();
  /** React's onChange: the automation with its grants kept. */
  readonly detailChange = output<AutomationDetail>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly now = inject(NowService).now;
  protected readonly originText = originText;
  protected readonly dayTime = dayTime;
  protected readonly versions = signal<AutomationVersionInfo[] | null>(null);
  protected readonly before = signal<AutomationDefinition | null>(null);
  protected readonly keeping = signal(false);
  protected readonly since = computed(() => this.detail().grants_set_version);
  protected readonly saved = computed(() => {
    const since = this.since();
    return (this.versions() ?? []).filter((v) => since === null || v.version > since);
  });
  protected readonly diff = computed(() => {
    const before = this.before();
    return before ? diffDefinitions(before, this.detail().definition) : null;
  });
  protected readonly labels = computed(() => ({ before: `v${this.since()}`, after: `v${this.detail().version}` }));
  protected readonly text = computed(() => {
    const d = this.detail();
    const since = this.since();
    return ` v${d.version} was saved after they were set${since !== null ? ` in v${since}` : ''}, so its runs ask you for everything and approvals offer no remember until you keep them. Editing a grant keeps them too.`;
  });
  private readonly key = computed(() => `${this.detail().id}:${this.detail().version}:${this.since()}`);
  private seq = 0;

  constructor() {
    effect(() => {
      this.key();
      const id = this.detail().id;
      const since = this.since();
      const n = ++this.seq;
      this.bridge
        .call('automations.versions', { id })
        .then((list) => {
          if (n === this.seq) this.versions.set(list);
        })
        .catch((err: unknown) => this.toasts.error(err));
      if (since !== null) {
        this.bridge
          .call('automations.version', { id, version: since })
          .then((v) => {
            if (n === this.seq) this.before.set(v.definition);
          })
          .catch((err: unknown) => this.toasts.error(err));
      }
    });
  }

  protected async keep(): Promise<void> {
    this.keeping.set(true);
    const d = this.detail();
    try {
      this.detailChange.emit(await this.bridge.call('automations.keepGrants', { id: d.id }));
      this.toasts.toast({ tone: 'info', message: `Grants kept for v${d.version}.` });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.keeping.set(false);
    }
  }
}

/** Grants (spec §8.4): what this automation's runs may do without asking, where each rule came from, and the suspension banner. */
@Component({
  selector: 'div[deskGrantsView]',
  imports: [Button, EmptyState, GrantEditor, SuspendedBanner],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-grants' },
  template: `
    @if (detail().grants_suspended) {
      <section deskSuspendedBanner [detail]="detail()" (detailChange)="detailChange.emit($event)"></section>
    }
    <p class="muted small">Grants go before the project's policy, for this automation's scripts and step agents only.</p>
    @if (detail().grants.length === 0 && editing() === null) {
      <div deskEmptyState title="No grants" body="Its runs ask you for whatever the project's policy does not allow. Approve and remember adds a grant."></div>
    }
    <ul class="auto-grant-list">
      @for (g of rows(); track g.key; let i = $index) {
        <li class="auto-grant" [attr.aria-label]="g.text">
          @if (editing() === i) {
            <form deskGrantEditor [initial]="g.grant" [busy]="busy()" (save)="save(replace(i, [$event]))" (cancel)="editing.set(null)"></form>
          } @else {
            <div class="grow">
              <p class="auto-grant-rule" [class]="g.grant.action">{{ g.text }}</p>
              <p class="muted small">{{ g.origin }}@if (g.runHref; as runHref) { · <a class="link" [href]="runHref">Open run</a>}</p>
            </div>
            @if (g.wide; as wide) {
              <button deskButton size="sm" variant="ghost" [disabled]="busy()" [attr.title]="wide.title" (click)="save(replace(i, wide.grants))">{{ wide.label }}</button>
            }
            <button deskButton size="sm" variant="ghost" [disabled]="busy()" (click)="editing.set(i)">Edit</button>
            <button deskButton size="sm" variant="ghost" [disabled]="busy()" (click)="save(replace(i, []))">Remove</button>
          }
        </li>
      }
    </ul>
    @if (editing() === detail().grants.length) {
      <form deskGrantEditor [busy]="busy()" (save)="save(replace(detail().grants.length, [$event]))" (cancel)="editing.set(null)"></form>
    } @else {
      <div><button deskButton [disabled]="busy()" (click)="editing.set(detail().grants.length)">Add grant</button></div>
    }
  `,
})
export class GrantsView {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly detail = input.required<AutomationDetail>();
  /** React's onChange. */
  readonly detailChange = output<AutomationDetail>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly now = inject(NowService).now;
  /** The row being edited; `detail().grants.length` is a new one. */
  protected readonly editing = signal<number | null>(null);
  protected readonly busy = signal(false);
  private readonly origins = computed(() => grantOrigins(this.s().events, this.detail().id));
  protected readonly rows = computed(() => {
    const d = this.detail();
    const now = this.now();
    return d.grants.map((grant) => {
      const origin = this.origins().get(grantKey(grant));
      const wide = widenedGrants(grant);
      const domain = wide?.[0]?.match?.domain;
      return {
        key: grantKey(grant),
        grant,
        text: describeGrant(grant),
        origin: grantOriginText(origin, d.definition, now),
        runHref: origin?.kind === 'remembered' ? href({ name: 'project', id: this.projectId(), tab: 'automations', automationId: d.id, view: 'runs', runId: origin.run_id }) : null,
        wide: wide ? { grants: wide, label: `Widen to ${domain}`, title: `Allow ${domain} and every subdomain of it, not only ${grant.match?.domain}` } : null,
      };
    });
  });

  protected replace(i: number, next: Grant[]): Grant[] {
    return replaceGrant(this.detail().grants, i, next);
  }

  protected async save(grants: Grant[]): Promise<void> {
    this.busy.set(true);
    try {
      this.detailChange.emit(await this.bridge.call('automations.setGrants', { id: this.detail().id, grants, reason: 'edited' }));
      this.editing.set(null);
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(false);
    }
  }
}
