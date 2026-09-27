import { ChangeDetectionStrategy, Component, computed, DestroyRef, effect, inject, input, linkedSignal, output, signal, untracked, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, AutomationDetail, AutomationLayout, StepKind, ValidationIssue } from '@desk/protocol';
import {
  addStep,
  autoLayout,
  blankDefinition,
  connect,
  diffDefinitions,
  ensureLayout,
  mapIssues,
  moveNodes,
  originText,
  removeSelection,
  STEP_KIND_LABEL,
  whenText,
  type AutomationDoc,
  type GraphSelection,
} from '@desk/ui-core';
import { Button } from '../../components/button';
import { ToastService } from '../../components/toast';
import { DeskBridge, DeskCallError } from '../../core/desk-bridge';
import { RouteService } from '../../core/route.service';
import { ConflictDialog, type Conflict } from './conflict-dialog';
import { EdgeInspector } from './edge-inspector';
import { GraphCanvas } from './graph-canvas';
import { SettingsInspector } from './settings-inspector';
import { StartInspector } from './start-inspector';
import { StepInspector } from './step-inspector';

const KINDS: StepKind[] = ['script', 'agent', 'ask', 'wait', 'automation', 'tell_desk'];
const KIND_CLASS: Record<StepKind, string> = { script: 'k-script', agent: 'k-agent', ask: 'k-ask', wait: 'k-wait', automation: 'k-automation', tell_desk: 'k-tell' };
type Validation = { errors: ValidationIssue[]; warnings: ValidationIssue[]; next_times: Record<string, string[]> };
const NO_ISSUES: Validation = { errors: [], warnings: [], next_times: {} };

const docOf = (def: AutomationDefinition, layout: AutomationLayout): AutomationDoc => ({ def, layout: ensureLayout(def, layout) });

/**
 * Design (mockup 2): the canvas, the inspector, the add-step strip, validation, Save and conflicts. It edits a saved
 * automation (`detail`), or a Blank one before its first save (`draftName`). The screen remounts it for another automation.
 */
@Component({
  selector: 'div[deskDesignView]',
  imports: [Button, ConflictDialog, EdgeInspector, GraphCanvas, SettingsInspector, StartInspector, StepInspector],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-design' },
  template: `
    <div class="auto-design-main">
      <div class="auto-toolbar">
        <span class="auto-status" role="status" [class.bad]="problems() > 0">{{ status() }}</span>
        <span class="grow"></span>
        <button deskButton size="sm" variant="ghost" (click)="relayout({ def: doc().def, layout: autoLayout(doc().def) })">Tidy up</button>
        @if (detail() && dirty()) {
          <button deskButton size="sm" variant="ghost" (click)="discard()">Discard changes</button>
        }
        <input class="input auto-note" aria-label="Change note" placeholder="What changed (optional)" maxlength="2000" [value]="note()" (input)="note.set(val($event))" />
        <button deskButton variant="primary" size="sm" [pending]="saving()" [disabled]="!dirty() || !checked() || problems() > 0" [attr.title]="problems() ? 'Fix the problems first' : null" (click)="save()">Save</button>
      </div>
      <div
        deskGraphCanvas
        [def]="doc().def"
        [layout]="doc().layout"
        [startLabel]="startLabel()"
        [selection]="sel()"
        [issues]="issues()"
        [editable]="true"
        (pick)="selection.set($event)"
        (moved)="relayout(moveNodes(doc(), $event))"
        (connect)="onConnect($event.from, $event.to)"
        (remove)="remove($event.steps, $event.edges)"
      ></div>
      <div class="auto-strip" role="toolbar" aria-label="Add a step">
        <span class="muted small">Add a step</span>
        @for (k of kinds; track k) {
          <button type="button" class="auto-add" [class]="kindClass[k]" (click)="add(k)">{{ kindLabel[k] }}</button>
        }
      </div>
    </div>
    @switch (sel().kind) {
      @case ('start') {
        <aside deskStartInspector [doc]="doc()" [errors]="issues().start" [nextTimes]="validation().next_times" (docChange)="doc.set($event)"></aside>
      }
      @case ('step') {
        @for (id of stepKey(); track id) {
          <aside
            deskStepInspector
            [projectId]="projectId()"
            [doc]="doc()"
            [stepId]="id"
            [selfName]="name()"
            [sources]="sources()"
            [errors]="issues().steps[id] ?? noErrors"
            (docChange)="doc.set($event)"
            (renamed)="selection.set({ kind: 'step', id: $event })"
            (remove)="remove([id], [])"
          ></aside>
        }
      }
      @case ('edge') {
        <aside deskEdgeInspector [doc]="doc()" [index]="edgeKey()" [errors]="issues().edges[edgeKey()] ?? noErrors" (docChange)="doc.set($event)" (remove)="remove([], [edgeKey()])"></aside>
      }
      @default {
        <aside deskSettingsInspector [doc]="doc()" [issues]="issues().general" [warnings]="validation().warnings" (docChange)="doc.set($event)"></aside>
      }
    }
    @if (conflict(); as c) {
      <div deskConflictDialog [conflict]="c" [mine]="doc().def" [saving]="saving()" (saveMine)="save(c.theirs.version)" (discardMine)="discardMine()" (close)="conflict.set(null)"></div>
    }
  `,
})
export class DesignView {
  readonly projectId = input.required<string>();
  readonly sources = input.required<Array<{ id: string; label: string }>>();
  readonly detail = input<AutomationDetail | null>(null);
  readonly draftName = input<string | null>(null);
  /** React's onChange: a saved automation's new detail (a save, or theirs after Discard mine). */
  readonly detailChange = output<AutomationDetail>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  protected readonly kinds = KINDS;
  protected readonly kindClass = KIND_CLASS;
  protected readonly kindLabel = STEP_KIND_LABEL;
  protected readonly autoLayout = autoLayout;
  protected readonly moveNodes = moveNodes;
  protected readonly noErrors: string[] = [];

  protected readonly name = computed(() => this.detail()?.name ?? this.draftName() ?? '');
  /** Read once, on first use (React's useState initializer): a later detail is adopted by the effect below, not here. */
  protected readonly doc = linkedSignal<AutomationDoc>(() =>
    untracked(() => {
      const d = this.detail();
      return d ? docOf(d.definition, d.layout) : docOf(blankDefinition(this.name()), {});
    }),
  );
  protected readonly saved = linkedSignal<{ def: AutomationDefinition; version: number } | null>(() =>
    untracked(() => {
      const d = this.detail();
      return d ? { def: d.definition, version: d.version } : null;
    }),
  );
  protected readonly selection = signal<GraphSelection>({ kind: 'none' });
  protected readonly validation = signal<Validation>(NO_ISSUES);
  protected readonly checked = signal(false);
  protected readonly note = signal('');
  protected readonly saving = signal(false);
  protected readonly conflict = signal<Conflict | null>(null);

  /** The definition alone: moving a node changes the layout, which neither validation nor dirtiness looks at. */
  private readonly def = computed(() => this.doc().def);
  protected readonly dirty = computed(() => {
    const s = this.saved();
    return !s || !diffDefinitions(s.def, this.def()).empty;
  });
  protected readonly issues = computed(() => mapIssues(this.def(), this.validation().errors));
  protected readonly problems = computed(() => this.validation().errors.length);
  protected readonly sel = computed((): GraphSelection => {
    const s = this.selection();
    const def = this.def();
    return (s.kind === 'step' && !def.steps.some((x) => x.id === s.id)) || (s.kind === 'edge' && !def.edges[s.index]) ? { kind: 'none' } : s;
  });
  protected readonly stepKey = computed(() => {
    const s = this.sel();
    return s.kind === 'step' ? [s.id] : [];
  });
  protected readonly edgeKey = computed(() => {
    const s = this.sel();
    return s.kind === 'edge' ? s.index : 0;
  });
  protected readonly status = computed(() => {
    const n = this.problems();
    if (!this.checked()) return 'Checking…';
    if (n) return `${n} problem${n === 1 ? '' : 's'}`;
    return this.dirty() ? 'Ready to save' : `Saved as v${this.saved()?.version ?? 1}`;
  });
  protected readonly startLabel = computed(() => {
    const triggers = this.def().triggers;
    return triggers.length ? `${whenText(triggers)} · or Run now` : 'Run now only';
  });

  private gen = 0;
  private layoutTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    // Validation, 400 ms after the last edit; an answer for an older draft is dropped.
    effect((onCleanup) => {
      const definition = this.def();
      const projectId = this.projectId();
      const name = this.name();
      const g = ++this.gen;
      this.checked.set(false);
      const t = setTimeout(() => {
        this.bridge
          .call('automations.validate', { projectId, req: { definition, name } })
          .then((r) => {
            if (g !== this.gen) return;
            this.validation.set(r);
            this.checked.set(true);
          })
          .catch(() => {});
      }, 400);
      onCleanup(() => clearTimeout(t));
    });

    // Another save landed (Desk, the CLI, a restore): adopt it unless the user has edits, which Save will meet as a 409.
    const version = computed(() => this.detail()?.version);
    effect(() => {
      const v = version();
      untracked(() => {
        const d = this.detail();
        const saved = this.saved();
        if (!d || v === undefined || !saved || v === saved.version || this.dirty()) return;
        this.saved.set({ def: d.definition, version: d.version });
        this.doc.update((x) => docOf(d.definition, x.layout));
      });
    });

    inject(DestroyRef).onDestroy(() => clearTimeout(this.layoutTimer));
  }

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  /** Positions save on their own, 800 ms after the last move. */
  private saveLayout(layout: AutomationLayout): void {
    const d = this.detail();
    if (!d) return;
    clearTimeout(this.layoutTimer);
    this.layoutTimer = setTimeout(() => void this.bridge.call('automations.layout', { id: d.id, layout }).catch((err: unknown) => this.toasts.error(err)), 800);
  }

  protected relayout(next: AutomationDoc): void {
    this.doc.set(next);
    this.saveLayout(next.layout);
  }

  protected add(kind: StepKind): void {
    const s = this.sel();
    const r = addStep(this.doc(), kind, s.kind === 'step' ? s.id : null);
    this.relayout(r.doc);
    this.selection.set({ kind: 'step', id: r.id });
  }

  protected remove(steps: string[], edges: number[]): void {
    this.doc.set(removeSelection(this.doc(), steps, edges));
    this.selection.set({ kind: 'none' });
  }

  protected onConnect(from: string, to: string): void {
    const r = connect(this.doc(), from, to);
    if ('error' in r) this.toasts.toast({ tone: 'error', message: r.error });
    else this.doc.set(r.doc);
  }

  protected discard(): void {
    const s = this.saved();
    if (!s) return;
    this.doc.set(docOf(s.def, this.doc().layout));
    this.selection.set({ kind: 'none' });
  }

  protected async save(base?: number): Promise<void> {
    const detail = this.detail();
    const sent = this.doc().def;
    const note = this.note().trim();
    const changeNote = note ? { change_note: note } : {};
    this.saving.set(true);
    try {
      if (!detail) {
        const r = await this.bridge.call('automations.create', { projectId: this.projectId(), req: { name: this.name(), definition: sent, via: 'editor', ...changeNote } });
        await this.bridge.call('automations.layout', { id: r.automation.id, layout: this.doc().layout });
        this.toasts.toast({ tone: 'info', message: `Saved ${r.automation.title}. It stays off until you turn it on.` });
        this.routes.navigate({ name: 'project', id: this.projectId(), tab: 'automations', automationId: r.automation.id, view: 'design' });
        return;
      }
      const r = await this.bridge.call('automations.save', { id: detail.id, req: { definition: sent, base_version: base ?? this.saved()!.version, via: 'editor', ...changeNote } });
      void this.bridge.call('automations.layout', { id: detail.id, layout: this.doc().layout }).catch(() => {});
      this.saved.set({ def: r.automation.definition, version: r.automation.version });
      this.doc.update((d) => (d.def === sent ? { ...d, def: r.automation.definition } : d));
      this.note.set('');
      this.conflict.set(null);
      this.detailChange.emit(r.automation);
      if (r.warnings.length) this.toasts.toast({ tone: 'info', message: `Saved v${r.automation.version} with ${r.warnings.length} warning${r.warnings.length === 1 ? '' : 's'}: see the automation's settings.` });
    } catch (err) {
      if (detail && err instanceof DeskCallError && err.status === 409) {
        try {
          const [theirs, versions] = await Promise.all([this.bridge.call('automations.get', { id: detail.id }), this.bridge.call('automations.versions', { id: detail.id })]);
          this.conflict.set({ theirs, by: versions[0] ? originText(versions[0]) : 'someone' });
        } catch (e) {
          this.toasts.error(e);
        }
      } else this.toasts.error(err);
    } finally {
      this.saving.set(false);
    }
  }

  protected discardMine(): void {
    const c = this.conflict();
    if (!c) return;
    this.saved.set({ def: c.theirs.definition, version: c.theirs.version });
    this.doc.set(docOf(c.theirs.definition, this.doc().layout));
    this.conflict.set(null);
    this.selection.set({ kind: 'none' });
    this.detailChange.emit(c.theirs);
  }
}
