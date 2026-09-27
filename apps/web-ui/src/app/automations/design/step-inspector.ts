import { ChangeDetectionStrategy, Component, computed, input, linkedSignal, output, ViewEncapsulation } from '@angular/core';
import type { OnError, Step } from '@desk/protocol';
import { incomingCount, patchStep, renameStep, routeProblem, STEP_KIND_LABEL, templateSuggestions, type AutomationDoc } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { ListEditor } from './list-editor';
import { StepKindFields } from './step-kind-fields';

const retries = (e: OnError): number | null => (typeof e === 'object' ? e.retry : null);

/** The selected step's fields (spec §8.2): common ones, then its kind's. */
@Component({
  selector: 'aside[deskStepInspector]',
  imports: [Button, Field, ListEditor, StepKindFields],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-inspector', 'aria-label': 'Step' },
  template: `
    @if (step(); as step) {
      <p class="eyebrow">{{ kindLabel() + ' step' }}</p>
      @if (errors().length || idError()) {
        <ul class="auto-issues" role="alert">
          @if (idError()) {
            <li>{{ idError() }}</li>
          }
          @for (e of errors(); track e) {
            <li>{{ e }}</li>
          }
        </ul>
      }
      <div deskField id="step-title" label="Title">
        <input id="step-title" class="input" [value]="step.title" (input)="patch({ title: val($event) })" />
      </div>
      <div deskField id="step-id" label="Id" hint="Templates name this step by it; renaming updates them.">
        <input id="step-id" class="input mono" [value]="idDraft()" (input)="idDraft.set(val($event))" (blur)="commitId()" (keydown.enter)="commitId()" />
      </div>
      <div deskStepKindFields [projectId]="projectId()" [selfName]="selfName()" [sources]="sources()" [step]="step" [suggest]="suggest()" (patch)="patch($event)"></div>
      @if (joins()) {
        <div deskField id="step-join" label="With several steps before it">
          <select id="step-join" class="select" (change)="setJoin(val($event))">
            <option value="all" [selected]="step.join === 'all'">Wait for all of them, run if one led here</option>
            <option value="any" [selected]="step.join === 'any'">Run on the first that leads here</option>
          </select>
        </div>
      }
      <div class="auto-inline">
        <div deskField id="step-on-error" label="If it fails">
          <select id="step-on-error" class="select" (change)="setOnError(val($event))">
            <option value="stop" [selected]="mode() === 'stop'">Stop the run</option>
            <option value="continue" [selected]="mode() === 'continue'">Continue on route error</option>
            <option value="retry" [selected]="mode() === 'retry'">Try again</option>
          </select>
        </div>
        @if (mode() === 'retry') {
          <div deskField id="step-retries" label="Attempts after the first">
            <input id="step-retries" class="input" type="number" min="1" max="3" [value]="attempts()" (input)="setRetries(val($event))" />
          </div>
        }
      </div>
      @if (chooses()) {
        <div deskField id="step-timeout" label="Time limit in minutes (optional)" [hint]="step.kind === 'script' ? 'Default 10.' : 'Default 60.'">
          <input id="step-timeout" class="input" type="number" min="1" max="1440" [value]="step.timeout_min ?? ''" (input)="setTimeoutMin(val($event))" />
        </div>
        <fieldset deskListEditor id="step-routes" label="Routes" [values]="step.routes" [check]="routeProblem" addLabel="Add route" [max]="10" hint="Named outcomes it can choose. A route no edge takes ends that branch." (valuesChange)="patch({ routes: $event })"></fieldset>
        <fieldset deskListEditor id="step-publish" label="Publish to the Library" [values]="step.publish" placeholder="digest.md" addLabel="Add file" [max]="20" hint="Globs in its folder, copied to the Library after it succeeds." (valuesChange)="patch({ publish: $event })"></fieldset>
      }
      <div><button deskButton variant="danger" size="sm" (click)="remove.emit()">Delete step</button></div>
    }
  `,
})
export class StepInspector {
  readonly projectId = input.required<string>();
  readonly doc = input.required<AutomationDoc>();
  readonly stepId = input.required<string>();
  /** This automation's name: Run automation cannot pick it. */
  readonly selfName = input.required<string>();
  /** Git sources, for an agent's worktree. */
  readonly sources = input.required<Array<{ id: string; label: string }>>();
  readonly errors = input.required<string[]>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();
  /** The step's id changed: the selection should follow it. */
  readonly renamed = output<string>();
  /** React's onDelete. */
  readonly remove = output<void>();

  protected readonly routeProblem = routeProblem;
  protected readonly step = computed(() => this.doc().def.steps.find((s) => s.id === this.stepId()));
  protected readonly idDraft = linkedSignal(() => this.stepId());
  protected readonly idError = linkedSignal<string, string | null>({ source: this.stepId, computation: () => null });
  protected readonly kindLabel = computed(() => {
    const s = this.step();
    return s ? STEP_KIND_LABEL[s.kind] : '';
  });
  protected readonly suggest = computed(() => templateSuggestions(this.doc().def, this.stepId()));
  protected readonly joins = computed(() => incomingCount(this.doc().def, this.stepId()) >= 2);
  protected readonly mode = computed(() => {
    const e = this.step()?.on_error ?? 'stop';
    return typeof e === 'object' ? 'retry' : e;
  });
  protected readonly attempts = computed(() => retries(this.step()?.on_error ?? 'stop') ?? 1);
  protected readonly chooses = computed(() => {
    const kind = this.step()?.kind;
    return kind === 'script' || kind === 'agent';
  });

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected patch(p: Partial<Step>): void {
    const step = this.step();
    if (step) this.docChange.emit(patchStep(this.doc(), step.id, p));
  }

  protected commitId(): void {
    const step = this.step();
    if (!step) return;
    const next = this.idDraft().trim();
    const r = renameStep(this.doc(), step.id, next);
    if ('error' in r) {
      this.idError.set(r.error);
      return;
    }
    this.idError.set(null);
    if (next !== step.id) {
      this.docChange.emit(r.doc);
      this.renamed.emit(next);
    }
  }

  protected setJoin(v: string): void {
    this.patch({ join: v as Step['join'] });
  }

  protected setOnError(v: string): void {
    this.patch({ on_error: v === 'retry' ? { retry: 1 } : (v as 'stop' | 'continue') });
  }

  protected setRetries(v: string): void {
    this.patch({ on_error: { retry: Math.min(3, Math.max(1, Number(v) || 1)) } });
  }

  protected setTimeoutMin(v: string): void {
    this.patch({ timeout_min: v === '' ? undefined : Number(v) });
  }
}
